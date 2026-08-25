# Da EPUB a WAV

Pipeline in due passi: l'EPUB diventa un JSONL di segmenti di testo, il JSONL
diventa un WAV per segmento piu' un `manifest.jsonl`. L'assemblaggio finale in
M4B non e' ancora scritto (vedi in fondo).

## Ambiente

```bash
cd /home/claude/chatterbox
source venv-cb/bin/activate
```

Controllo rapido che il venv sia sano:

```bash
python -c "import torch, chatterbox; print(torch.__version__, torch.cuda.is_available())"
```

Deve stampare `2.6.0+cu124 True`. Se dice `ModuleNotFoundError` o parte il
Python di sistema invece del 3.11, il venv e' scollegato dal suo interprete:
vedi "Se sposti il progetto" in fondo.

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

`eccezioni.json` e' un dizionario `parola -> come va letta`, applicato prima
della sintesi con un match su confine di parola:

```json
{
  "ADHD": "addìaccadì",
  "Macchiavelli": "Machiavelli"
}
```

Va rivisto per ogni libro: quello nel repo e' rimasto dal titolo precedente.
Tipicamente serve per sigle, nomi propri e forestierismi.

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
| `-e`, `--eccezioni` | `eccezioni.json` | dizionario di respelling |
| `--exaggeration` | 0.65 | espressivita' |
| `--cfg-weight` | 0.5 | aderenza al riferimento |
| `--temperature` | 0.6 | varianza |
| `-c`, `--chapter` | tutti | genera solo questo capitolo |
| `-n`, `--limite` | nessuno | genera al massimo N segmenti |
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

`sospetto: true` marca un segmento la cui durata si discosta troppo da quella
attesa dal numero di caratteri (sotto il 60% o sopra il 180%). In corsa
compaiono come `<-- CONTROLLA`. Di solito e' un troncamento: Chatterbox chiude
la frase in anticipo. Per contarli a fine corsa:

```bash
grep -c '"sospetto": true' audio_libro/manifest.jsonl
```

Se ce ne sono molti, la contromisura sarebbe alzare `repetition_penalty` a
2.2-2.5, ma quel parametro **non e' ancora esposto** dallo script.

## Passo 3: WAV -> M4B

Non ancora disponibile. `assembla_m4b.py` e' da scrivere, la specifica sta in
`CLAUDE.md`. Per ora il prodotto della pipeline sono i WAV per segmento piu'
il manifest.

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
