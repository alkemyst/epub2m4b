# Da EPUB ad audiolibro

Pipeline in tre passi: l'EPUB diventa un JSONL di segmenti di testo, il JSONL
diventa un WAV per segmento piu' un `manifest.jsonl`, e il manifest diventa un
M4B con capitoli, copertina e metadata.

```
                      references/ref.wav          (passo 0, una volta sola)
                              |
libro.epub  ->  libro.jsonl  -+->  audio_libro/*.wav + manifest.jsonl  ->  libro.m4b
            1                 2                                        3
```

Manca ancora la normalizzazione di loudness: il volume puo' variare fra un
segmento e l'altro. E' un rinvio consapevole, vedi `CLAUDE.md`.

## Gli script

| script | fa |
|---|---|
| `prepara_ref.py` | registrazione -> voce di riferimento normalizzata |
| `epub_to_jsonl.py` | EPUB -> JSONL di segmenti di testo |
| `tts_chatterbox.py` | JSONL -> un WAV per segmento, riprendibile |
| `assembla_m4b.py` | WAV + manifest -> M4B con capitoli e copertina |
| `sommario.py` | struttura del libro: quanto dura ogni capitolo |

`eccezioni.json` e' il dizionario di respelling, applicato prima della sintesi.

## Uso quotidiano

```bash
cd ~/Local/epub2m4b
source venv-cb/bin/activate
python -c "import torch, chatterbox; print(torch.__version__, torch.cuda.is_available())"
```

Deve stampare la versione di torch e `True`. Se dice `ModuleNotFoundError` o
parte il Python di sistema invece del 3.11, il venv e' scollegato dal suo
interprete: vedi "Se sposti il progetto" in fondo.

Per installare da zero, vedi "Setup da zero" alla fine.

## Passo 0: la voce di riferimento

Chatterbox clona la voce da un singolo WAV di pochi secondi. Serve prima di
tutto il resto, perche' senza non si sintetizza niente.

```bash
python prepara_ref.py registrazione.wav -o references/ref_nuova.wav \
    --da 1:24 --durata 8
```

Ritaglia 8 secondi a partire da 1:24, converte in mono 48kHz e normalizza la
loudness con `loudnorm` a due passate. Accetta qualsiasi sorgente che ffmpeg
sappia aprire, video compresi.

### Come scegliere il pezzo

Il taglio conta piu' di quanto sembri, perche' Chatterbox non usa tutto il
riferimento allo stesso modo:

- i **primi 6 secondi** alimentano i token di prompt, quelli che portano
  prosodia e timbro (`ENC_COND_LEN = 6 * 16000`);
- i **primi 10 secondi** alimentano il decoder (`DEC_COND_LEN = 10 * 24000`);
- il **file intero** alimenta l'embedding di speaker.

Conseguenze pratiche. Sotto i 6 secondi la voce resta poco caratterizzata.
Oltre i 10 l'audio in piu' influenza solo l'embedding, quindi allungare non
serve. E soprattutto: **i primi 6 secondi devono essere la parte migliore**,
non un respiro o un attacco incerto. Siccome pero' l'embedding legge fino in
fondo, anche la coda va pulita: silenzio, rumore o una seconda voce alla fine
sporcano il risultato lo stesso.

Il resto sono le solite cose: un solo parlante, niente musica o riverbero,
tono uguale a quello che vuoi in lettura, frasi intere e non parole isolate.

### Sceglierla a orecchio

La qualita' di un riferimento non si giudica dal file ma dal risultato, quindi
conviene preparare piu' candidati e sintetizzare lo stesso passaggio con
ognuno:

```bash
for v in references/ref_*.wav; do
  python tts_chatterbox.py libro.jsonl -o /tmp/voce_$(basename $v .wav) -r "$v" -n 5
done
```

Normalizzando tutti i candidati allo stesso target si evita che a orecchio
vinca semplicemente il piu' forte. Quello che convince diventa il default di
`tts_chatterbox.py` (`-r`), oggi `references/ref_stefano_buendia.wav`.

## Passo 1: EPUB -> JSONL

Prima guarda com'e' fatto il libro, senza scrivere niente:

```bash
python epub_to_jsonl.py "libro.epub" --lista-capitoli
```

Stampa una tabella `cap / segmenti / caratteri / titolo`. Serve a individuare i
documenti che non vanno narrati: indice, quarta di copertina, colophon, note di
copyright, catalogo editoriale in fondo.

Poi genera il JSONL escludendo quei numeri:

```bash
python epub_to_jsonl.py "libro.epub" -o libro.jsonl --salta-capitoli 1-4,29,30
```

`--salta-capitoli` accetta numeri singoli e intervalli. La numerazione degli
altri capitoli **non cambia**: escludendo 1-4 il primo capitolo narrato resta
`ch05`. E' voluto, perche' gli id sono la chiave di cache del passo 2: se
slittassero, cambiare la lista degli esclusi a sintesi gia' avviata farebbe
rigenerare tutto da capo.

A fine corsa lo script segnala i segmenti sotto i 10 caratteri, che sono quelli
su cui Chatterbox e' meno stabile. Battute di dialogo brevi (`- Prego?`) sono
normali e non vanno accorpate: in un audiolibro devono suonare separate.

Flag utili:

| flag | default | cosa fa |
|---|---|---|
| `-o`, `--out` | `<nome>.jsonl` | file di uscita |
| `--maxlen` | 280 | lunghezza massima di un segmento in caratteri |
| `--min-caratteri` | 200 | scarta i documenti piu' corti (copertine, colophon) |
| `--salta-capitoli` | vuoto | numeri da escludere, es. `1-4,29,30` |
| `--lista-capitoli` | | stampa la tabella ed esce |

### Respelling

Un dizionario di respelling e' un JSON `parola -> come va letta`, applicato al
testo prima della sintesi con un match su confine di parola:

```json
{
  "ADHD": "addìaccadì",
  "Macchiavelli": "Machiavelli"
}
```

Il respelling si applica al passo 2, non qui: e' `tts_chatterbox.py` a leggere i
dizionari. Sta in questa sezione perche' si ragiona sul testo.

**Piu' dizionari insieme.** `-e` e' ripetibile e i file si fondono nell'ordine,
cosi' si separa quello che vale per tutti i libri da quello che vale per uno:

```bash
python tts_chatterbox.py libro.jsonl -o audio_libro \
    -e dizionari/accenti_it.json \
    -e dizionari/sigle.json \
    -e dizionari/buendia.json
```

A parita' di chiave **vince l'ultimo file**, quindi il dizionario del libro puo'
correggere una voce di quello generale senza ricopiarlo. Le sovrascritture
vengono elencate al lancio, cosi' un doppione non passa inosservato.

Senza `-e` vale il default `eccezioni.json`, e se non c'e' si tira dritto senza
respelling. Con `-e` i file sono stati chiesti apposta, quindi se uno manca lo
script si ferma. `--senza-eccezioni` disattiva tutto, default compreso.

Due regole di applicazione, che contano quando il dizionario e' grande:

- le chiavi piu' lunghe vanno per prime, cosi' fra `AD` e `ADHD` vince la piu'
  specifica invece di quella che capita prima;
- una chiave **tutta minuscola** vale anche a inizio frase, dove la parola e'
  maiuscola: `"tempo": "tèmpo"` prende anche `Tempo` e scrive `Tèmpo`. Le chiavi
  che contengono gia' una maiuscola (`ADHD`, `Macchiavelli`) restano esatte.

Cambiare dizionari non rigenera tutto: la firma di cache copre il testo dopo il
respelling, quindi si rifanno solo i segmenti che cambiano davvero.

## Passo 2: JSONL -> WAV

**Fai sempre prima un test corto.** Una sintesi completa e' molte ore di GPU, e
gli unici parametri che contano davvero (voce di riferimento, `exaggeration`)
si giudicano a orecchio:

```bash
python tts_chatterbox.py libro.jsonl -o audio_libro -c 5 -n 20
```

`-c 5` limita al capitolo 5, `-n 20` ai primi 20 segmenti. Ascolta, e se la
voce non convince cambia `-r` o `--exaggeration` e rilancia: la firma dei
parametri e' nel manifest, quindi i segmenti gia' fatti con parametri diversi
vengono rigenerati da soli.

Quando il risultato va bene, la corsa completa:

```bash
nohup python tts_chatterbox.py libro.jsonl -o audio_libro > tts.log 2>&1 &
tail -f tts.log
```

Dura ore, quindi conviene staccarla dal terminale. E' interrompibile con
Ctrl-C o riavvio della macchina senza perdere lavoro: rilanciando lo stesso
comando riprende da dove era arrivata.

Flag utili:

| flag | default | cosa fa |
|---|---|---|
| `-o`, `--outdir` | `audio` | cartella di uscita, ci finisce anche `manifest.jsonl` |
| `-r`, `--ref` | `references/ref_stefano_buendia.wav` | voce da clonare |
| `-e`, `--eccezioni` | `eccezioni.json` | dizionario di respelling, ripetibile |
| `--senza-eccezioni` | | nessun respelling, ignora anche il default |
| `--exaggeration` | 0.65 | espressivita' |
| `--cfg-weight` | 0.5 | aderenza al riferimento |
| `--temperature` | 0.6 | varianza |
| `-c`, `--chapter` | tutti | genera solo questo capitolo |
| `-n`, `--limite` | nessuno | genera al massimo N segmenti |
| `--solo-id` | | rigenera solo questi id, scavalcando la cache |
| `--solo-sospetti` | | rigenera gli id sospetti secondo il manifest |
| `--rivaluta` | | ricalcola i flag `sospetto` senza rigenerare audio |
| `--force` | | rigenera tutto ignorando il manifest |

### Ripresa e cache

`manifest.jsonl` nella cartella di uscita e' il registro di cosa e' gia' fatto.
Ogni riga porta una `firma`, hash del testo piu' i parametri di sintesi. Al
lancio lo script rigenera un segmento solo se manca dal manifest o se la firma
non torna. Quindi:

- interrompere e rilanciare riprende senza rifare nulla;
- correggere `eccezioni.json` rigenera **solo** i segmenti il cui testo cambia;
- cambiare voce o `exaggeration` rigenera tutto, perche' la firma dipende
  anche dai parametri.

Il manifest si apre in append, quindi un id puo' comparire piu' volte dopo una
rigenerazione. Vince l'ultima riga: e' voluto, tiene la storia dei tentativi.

### Segmenti sospetti

`sospetto: true` marca un segmento la cui durata reale non torna con quella
attesa dal testo. L'attesa e' `0.4s + caratteri/14`: la parte fissa e' il
contorno che Chatterbox mette sempre (attacco, respiro, coda di silenzio). La
finestra ammessa va dal 60% al 180% dell'attesa, piu' mezzo secondo di
tolleranza per parte. In corsa compaiono come `<-- CONTROLLA`. Di solito e' un
troncamento: Chatterbox chiude la frase in anticipo. Per contarli:

```bash
grep -c '"sospetto": true' audio_libro/manifest.jsonl
```

La parte fissa e la tolleranza assoluta servono ai segmenti brevi. Con la sola
proporzione, `- Eh?` avrebbe una finestra di `[0.21, 0.64]` secondi: nessuna
generazione reale ci sta dentro, quindi le battute di dialogo corte
risulterebbero sospette per sempre, comunque venga l'audio.

Resta un'euristica sulla sola durata: vede i troncamenti grossi, non le
allucinazioni che durano il giusto. Per quelle serve il QC con Whisper, ancora
da scrivere.

Se ce ne sono molti, la contromisura sarebbe alzare `repetition_penalty` a
2.2-2.5, ma quel parametro **non e' ancora esposto** dallo script.

**Il flag si toglie da solo.** Il manifest e' in append e vince l'ultima riga
per id, quindi se una rigenerazione viene bene il segmento smette di essere
sospetto senza dover fare niente. Quello che non si aggiorna da solo e' il flag
dei segmenti gia' prodotti quando le soglie cambiano: e' scritto nel manifest,
non ricalcolato. Per allinearlo senza rigenerare un secondo di audio:

```bash
python tts_chatterbox.py libro.jsonl -o audio_libro --rivaluta
```

Legge testo e durata dal manifest, ricalcola i flag con le soglie di adesso e
scrive solo quelli cambiati. `--solo-sospetti` ricalcola comunque per conto suo,
quindi non ha bisogno che tu lanci prima `--rivaluta`: serve perche'
`assembla_m4b.py` legge il flag salvato.

### Rifare i segmenti venuti male

Un segmento puo' venire male per due motivi, e si correggono in modo diverso.

**Il testo e' problematico** (sigla, nome proprio, punteggiatura strana):
correggi il testo nel JSONL o aggiungi una regola a `eccezioni.json`. La firma
cambia da sola e al lancio successivo si rigenera solo quel segmento. Non serve
nessun flag.

**Il testo va bene, e' uscita male la generazione:** la sintesi ha
`temperature=0.6`, quindi e' stocastica e a volte basta ritirare i dadi. Ma la
firma non cambia, quindi la cache non lo rifarebbe mai. Serve dirlo esplicito:

```bash
python tts_chatterbox.py libro.jsonl -o audio_libro --solo-id ch13_0207,ch07_0044
```

Rigenera solo quegli id ignorando la cache, e lascia intatto tutto il resto.
Ripetibile: rilanciando si ritirano di nuovo i dadi finche' non esce pulito.

Per lavorare sull'intera lista dei sospetti senza copiarla a mano:

```bash
python tts_chatterbox.py libro.jsonl -o audio_libro --solo-sospetti
```

Rilanciandolo la lista si accorcia: quelli venuti bene escono dal conteggio. Se
invece si allunga fra una corsa e l'altra, in mezzo e' girata una sintesi
normale che ha prodotto segmenti nuovi, `--solo-sospetti` da solo non ne puo'
aggiungere. E se un segmento non esce mai dalla lista per quante volte lo
ritiri, prima di insistere ascoltalo: probabile sia buono e falso positivo.

Se la lista e' lunga e vuoi sceglierne solo alcuni, `--solo-id` accetta anche un
file, dove il cancelletto commenta fino a fine riga:

```bash
python tts_chatterbox.py libro.jsonl -o audio_libro --solo-id @da_rifare.txt
```

```
# incollato dall'output di assembla_m4b.py
ch13_0207     # troncato a meta'
ch07_0044
```

**Non usare `-c 13 --force` per rifare qualche segmento del capitolo 13.**
Rigenera tutti e 333 i segmenti, e siccome la sintesi e' stocastica ritira i
dadi anche su quelli che erano venuti bene: un capitolo gia' validato puo'
tornare indietro peggiore. Nota che `-c 13` da solo invece non fa niente, perche'
il filtro per capitolo agisce prima del controllo di cache.

Le righe rigenerate si accodano al manifest e vincono sulle precedenti, quindi
`assembla_m4b.py` prende le durate nuove al prossimo assemblaggio senza altri
passaggi.

## Passo 3: WAV -> M4B

Prima un giro a vuoto, per vedere capitoli e durate senza aspettare la codifica:

```bash
python assembla_m4b.py audio_libro/manifest.jsonl -o libro.m4b \
    --epub "libro.epub" --dry-run
```

Stampa titolo, autore, copertina trovata e l'istante di inizio di ogni capitolo.
Se i confini tornano, togli `--dry-run`:

```bash
python assembla_m4b.py audio_libro/manifest.jsonl -o libro.m4b --epub "libro.epub"
```

`--epub` serve solo a pescare titolo, autore e copertina: titolo e autore dal
Dublin Core, la copertina da `ITEM_COVER` con fallback su `<meta name="cover">`
e poi su un'immagine chiamata "cover". Se l'EPUB e' incompleto o vuoi
sovrascrivere, ci sono `--titolo`, `--autore` e `--copertina`.

Flag utili:

| flag | default | cosa fa |
|---|---|---|
| `-o`, `--out` | `audiolibro.m4b` | file di uscita |
| `-j`, `--jsonl` | | JSONL di partenza, serve solo con i manifest vecchi (vedi sotto) |
| `--epub` | | da cui prendere titolo, autore e copertina |
| `--titolo`, `--autore`, `--copertina` | | sovrascrivono quel che viene dall'EPUB |
| `--narratore` | `Chatterbox IT, voce clonata` | finisce in `composer` e `comment` |
| `--bitrate` | `64k` | bitrate AAC |
| `--dry-run` | | prepara capitoli e lista senza codificare |

### Pause e capitoli

Fra un segmento e il successivo viene inserito un silenzio: 300ms di norma,
800ms dopo un segmento con `fine_paragrafo`, 1200ms a cambio capitolo. Il
silenzio di stacco e' conteggiato in coda al capitolo che si chiude, cosi' i
capitoli sono contigui e non restano buchi.

I titoli dei capitoli vengono dal campo `titolo`, quindi tipicamente
"Capitolo 1", "Capitolo 2", generati al passo 1.

### Se il manifest e' vecchio

I manifest generati prima dell'aggiunta di `titolo` e `fine_paragrafo` non
hanno i dati per le pause e per i titoli. Lo script se ne accorge e chiede il
JSONL di partenza:

```bash
python assembla_m4b.py audio_libro/manifest.jsonl -o libro.m4b -j libro.jsonl
```

Non serve rigenerare l'audio: i due campi non entrano nella firma di cache.

### Verifica finale

A fine codifica lo script confronta la durata reale del file con quella attesa
(parlato piu' pause). Oltre l'1% di scarto stampa un avviso ed esce con codice
1, perche' di solito vuol dire che manca dell'audio. Subito dopo elenca i
segmenti `sospetto` con id, durata e testo: non bloccano l'assemblaggio, ma
vanno riascoltati prima di considerare il libro finito.

Il file esce AAC mono 24kHz, con copertina incorporata e `media_type=2`, che e'
il tag che fa comparire il file come audiolibro invece che come musica.

**Nota:** manca ancora la normalizzazione di loudness, quindi il volume puo'
variare fra un segmento e l'altro. E' un rinvio consapevole, non un bug.

## Controllare la struttura del libro

`sommario.py` stampa un capitolo per riga con durata, posizione e peso sul
totale. Funziona su due sorgenti:

```bash
python sommario.py libro.m4b                      # a cose fatte, legge i capitoli col ffprobe
python sommario.py audio_libro/manifest.jsonl     # prima di codificare, stima dalle durate
```

```
 cap  titolo        inizio   durata      %   segm   sosp
--------------------------------------------------------
   5  Capitolo 1      0:00    22:17  30.9%    120
   6  Capitolo 2     22:17    25:30  35.4%    140      7
   7  Capitolo 3     47:47    22:06  30.7%    130      5
   8  Epilogo      1:09:53     2:09   3.0%     12      1  <-- corto
--------------------------------------------------------
4 capitoli, totale 1:12:02 (1.20 h), mediana 22:17
402 segmenti, parlato 1:09:27 + pause 2:35
13 segmenti sospetti, 3.2% del totale
```

Dal manifest escono anche le colonne `segm` e `sosp` (segmenti e sospetti per
capitolo), utili per capire **dove** si concentrano i problemi invece di sapere
solo quanti sono. Dal M4B escono in piu' titolo, autore e bitrate reale.

I capitoli molto fuori dalla mediana sono marcati `<-- corto` o `<-- lungo`. Il
confronto e' con la mediana e non con la media, perche' bastano un paio di
capitoli sbagliati per spostare la media e non far notare piu' niente. Non e'
un errore di per se': un epilogo corto e' normale. E' un errore se un capitolo
di narrativa dura un decimo degli altri, di solito perche' la sintesi si e'
interrotta li' o perche' del front matter e' finito dentro.

`--csv` stampa le stesse righe separate da punto e virgola, per aprirle altrove.

La durata dal manifest e' una stima: somma le durate dei WAV piu' le pause che
`assembla_m4b.py` inserira'. Torna con il file finito a meno di qualche
decimo. Per i confini dei capitoli senza stampare la tabella c'e' anche
`assembla_m4b.py --dry-run`, che pero' non guarda ne' segmenti ne' sospetti.

### CPU o GPU?

La codifica e' **tutta CPU**, e non c'e' niente da guadagnare a spostarla. La
GPU accelera il video (NVENC), non l'audio: un encoder AAC su GPU non esiste.
Non serve comunque: AAC mono a 24kHz gira a circa 200 volte il tempo reale.
Misurato qui, 30 minuti di audio codificati in 8.5 secondi, quindi un libro da
12 ore sono un paio di minuti. Il grosso del lavoro di `assembla_m4b.py` non e'
nemmeno l'encoder, e' leggere migliaia di WAV dal disco.

La GPU serve al passo 2, la sintesi, che e' l'unica parte che dura ore.

## Setup da zero

Testato su Ubuntu con Python di sistema 3.14 e GTX 1660 Ti (Turing, sm_75),
driver 580.

### 1. Prerequisiti di sistema

```bash
sudo apt install ffmpeg
python3 -V
```

`ffmpeg` serve a `prepara_ref.py` e ad `assembla_m4b.py`, e non passa da pip.

Se il Python di sistema e' 3.13 o piu' recente, **non usarlo**: torch pubblica
le ruote per le versioni nuove con mesi di ritardo, e le dipendenze di
chatterbox (transformers, numpy pinnato, librosa, s3tokenizer) non si risolvono.
Serve un 3.11 a fianco, senza toccare il sistema.

### 2. Python 3.11 e venv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.11
cd ~/Local/epub2m4b
uv venv --python 3.11 venv-cb
source venv-cb/bin/activate
python -V        # deve dire 3.11.x
```

Due trappole di `uv`:

- cerca automaticamente una dir chiamata `.venv`. Questa si chiama `venv-cb`,
  quindi va tenuta attivata (uv rispetta `VIRTUAL_ENV`) oppure va passato ogni
  volta `--python venv-cb/bin/python`. Se te ne dimentichi, uv usa o crea un
  altro ambiente senza dirtelo in modo evidente.
- `uv venv` non installa `pip` dentro il venv. O usi `uv pip install` al posto
  di `pip install` per tutto (piu' veloce nella risoluzione), oppure
  `uv pip install pip` una volta e poi usi pip normalmente.

### 3. Torch con CUDA, prima di tutto il resto

```bash
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu126
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Deve stampare `True` e il nome della GPU. Se stampa `False` ti e' arrivata la
build CPU e non ha senso proseguire.

Va installato **per primo**: chatterbox pinna torch, transformers e numpy, e se
lo lasci risolvere a lui puo' tirarsi dentro una ruota CPU.

### 4. Chatterbox

```bash
uv pip install chatterbox-tts
```

Poi verifica subito le due cose che possono essersi rotte:

```bash
python -c "import torch; print(torch.cuda.is_available())"
python -c "from chatterbox.mtl_tts import ChatterboxMultilingualTTS as M; import inspect; print('t3_model' in inspect.signature(M.from_pretrained).parameters)"
```

La prima deve essere ancora `True`. Se la risoluzione ha sostituito torch con
la build CPU, ripeti il punto 3 con `--force-reinstall`.

La seconda e' quella che conta per questi script, che chiamano
`from_pretrained(..., t3_model="v3")`. Il multilingue V3 e' recente e la ruota
su PyPI puo' essere indietro. Se stampa `False`, installa dal sorgente:

```bash
uv pip install "git+https://github.com/resemble-ai/chatterbox.git"
```

### 5. Il resto della pipeline

```bash
uv pip install ebooklib beautifulsoup4 lxml
```

### 6. Prova a vuoto

Prima di lanciare migliaia di segmenti, un giro corto che scarica i pesi e
scrive qualche WAV:

```bash
python tts_chatterbox.py libro.jsonl -o /tmp/prova -n 3
```

I pesi arrivano da Hugging Face, sono qualche GB e finiscono in
`~/.cache/huggingface`: la prima esecuzione e' lenta a prescindere dalla GPU.

### 7. Congelare

```bash
uv pip freeze > requirements.txt
```

Il freeze **non conserva l'index-url di torch**: sulla macchina successiva rifai
il punto 3 a mano e solo dopo `uv pip install -r requirements.txt`.

### Note

Con 6 GB di VRAM il modello multilingue ci sta ma e' al limite. Se compare un
OOM a meta' di una sessione lunga:

```bash
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

Non serve pinnare `setuptools<81`: era necessario quando `perth` importava
`pkg_resources`, ma `resemble-perth 1.0.1` non lo fa piu' e il watermarker gira
con setuptools 84.

## Se sposti il progetto

`venv-cb` non e' autosufficiente: punta a un Python 3.11 gestito da `uv` che
sta in `~/.local/share/uv/python/`, fuori dalla cartella del progetto. Copiare
o tarrare solo `chatterbox/` lascia il venv senza interprete.

Sulla macchina nuova:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv python install 3.11
```

`uv` crea il symlink `cpython-3.11-linux-x86_64-gnu` che il venv si aspetta.
Poi vanno riscritti i path assoluti rimasti dentro il venv:

```bash
VECCHIO=/percorso/vecchio/chatterbox
NUOVO=$(pwd)
ln -sfn ~/.local/share/uv/python/cpython-3.11-linux-x86_64-gnu/bin/python3.11 venv-cb/bin/python
sed -i "s|/home/UTENTEVECCHIO|$HOME|g" venv-cb/pyvenv.cfg
grep -rl "$VECCHIO" venv-cb/bin/ | while read f; do
  [ -L "$f" ] || sed -i "s|$VECCHIO|$NUOVO|g" "$f"
done
```

Serve il 3.11 esatto: le estensioni compilate in `site-packages` sono
`cpython-311`, con un altro minor non caricano. `venv-cb/lib/` invece non
contiene path assoluti, quindi i GB di `torch` e `chatterbox` restano validi e
non vanno riscaricati.
