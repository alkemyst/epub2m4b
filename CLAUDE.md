# CLAUDE.md

## Progetto
Pipeline EPUB -> audiolibro in italiano, TTS con Chatterbox Multilingual V3
(voice cloning), fallback Kokoro se Chatterbox risulta troppo instabile su
capitoli lunghi.

## Stato attuale
- `prepara_ref.py`: registrazione -> voce di riferimento mono 48kHz normalizzata
  (funzionante). Avvisa se il taglio esce dalla finestra utile 6-10s.
- `epub_to_jsonl.py`: EPUB -> JSONL di segmenti (funzionante, testato su EPUB reale).
  Flag `--lista-capitoli` (tabella dei capitoli, non scrive) e `--salta-capitoli 1-4,29,30`
  (esclude front e back matter). Riconosce le intestazioni numerate: `titolo` diventa
  "Capitolo 1" per l'M4B mentre il testo letto diventa "Capitolo uno.", perché dare `1.`
  da solo in pasto al TTS produce troncamenti.
- `tts_chatterbox.py`: JSONL -> WAV per segmento + manifest.jsonl riprendibile (funzionante).
  Il manifest porta anche `titolo` e `fine_paragrafo`, che servono all'assemblaggio.
  `--solo-id` e `--solo-sospetti` rigenerano singoli segmenti scavalcando la cache,
  `--rivaluta` ricalcola i flag `sospetto` senza rigenerare audio.
- `assembla_m4b.py`: manifest + WAV -> M4B con capitoli, copertina e metadata
  (funzionante, provato su audio sintetico). Ha `--dry-run` per vedere i confini
  dei capitoli senza codificare.
- `sommario.py`: struttura del libro, un capitolo per riga con durata e posizione
  (funzionante). Legge un M4B via ffprobe oppure un `manifest.jsonl`; dal manifest
  dà anche segmenti e sospetti per capitolo. Marca i capitoli fuori dalla mediana.
- `eccezioni.json`: dizionario respelling (ADHD -> "addì accadì", ecc.), applicato prima della sintesi.
  `-e` è ripetibile: più file si fondono nell'ordine e l'ultimo vince sui doppioni,
  così un vocabolario generale (accenti delle parole comuni) e uno per libro
  (nomi propri, sigle) convivono senza copiare regole. `--senza-eccezioni` li spegne tutti.
- `README.md`: comandi per girare la conversione, dall'EPUB all'M4B
- Normalizzazione di loudness: **rinviata**, vedi "Cosa manca"
- Controllo qualità automatico (Whisper su ogni segmento): **da scrivere**

### Scelta di progetto: gli id non si rinumerano
`--salta-capitoli` filtra **dopo** la numerazione, quindi escludendo i capitoli 1-4 il
primo capitolo narrato resta `ch05`. Gli id sono la chiave di cache di `manifest.jsonl`:
se slittassero, ritoccare la lista degli esclusi a sintesi avviata rigenererebbe ore di
audio già buono. La numerazione con buchi non si vede nel prodotto finale, perché
`assembla_m4b.py` ordina per `chapter` e l'etichetta visibile viene da `titolo`.

## Ambiente
- `venv-cb` (Python 3.11, uv), GPU GTX 1660 Ti 6GB, niente Tensor Core
- Il Python di sistema è 3.14, troppo avanti per torch e per le dipendenze di
  chatterbox: il 3.11 va installato con `uv python install 3.11`
- torch va installato **prima** di chatterbox, dall'index `cu126`, altrimenti la
  risoluzione può sostituirlo con la build CPU
- Chatterbox: provare PyPI, e passare al git solo se `from_pretrained` non
  accetta `t3_model` (il multilingue V3 è recente)
- ~~`setuptools<81` per `pkg_resources`~~: **non serve più**. `resemble-perth 1.0.1`
  non importa `pkg_resources`, verificato istanziando il watermarker con
  setuptools 84. Non reintrodurre il pin.
- Riferimento vocale: `ref_stefano_buendia.wav`, mono 48kHz, 6-10s, normalizzato
  con `loudnorm`. Si prepara con `prepara_ref.py`.
- Procedura di installazione completa in `README.md`, sezione "Setup da zero"

## Parametri Chatterbox di partenza
`language_id="it"`, `exaggeration=0.65`, `cfg_weight=0.5`, `temperature=0.6`,
`repetition_penalty=2.0`. Se ricompaiono troncamenti forzati (`forcing EOS
token` nel log), alzare `repetition_penalty` a 2.2-2.5.

Attenzione: `repetition_penalty` è documentato qui ma `tts_chatterbox.py` non lo
espone e non lo passa a `model.generate()`, quindi al momento quel rimedio non è
applicabile. Vedi "Cosa manca".

## Cosa manca per avere un audiolibro

La pipeline oggi arriva fino all'M4B. Restano aperte queste cose, in ordine di
quanto pesano sul risultato.

**Qualità dell'audio**

1. Normalizzazione di loudness: **rinviata per scelta esplicita**, non
   dimenticata. I segmenti escono a volume variabile e vanno uniformati con
   `loudnorm` a due passate, target circa -19 LUFS mono. Da decidere se
   applicarla per segmento durante il TTS (piu' semplice da riprendere) o in
   un passaggio unico sul concatenato (loudness piu' uniforme, ma due passate
   su tutto il libro).
2. `repetition_penalty` non e' esposto da `tts_chatterbox.py` ne' passato a
   `model.generate()`. E' il rimedio documentato contro i troncamenti forzati,
   quindi oggi quel consiglio non e' applicabile. Aggiungere il flag.
3. ~~Rigenerazione mirata~~: fatta, `--solo-id` e `--solo-sospetti` in
   `tts_chatterbox.py`. Vedi sotto.

**Controllo qualità**

4. QC con Whisper: trascrivere ogni WAV e confrontare con il testo di partenza
   (distanza di edit normalizzata). E' l'unico modo per beccare le allucinazioni
   e i troncamenti che la sola durata non vede. Il flag `sospetto` attuale e'
   solo un'euristica su durata attesa contro durata reale.
5. Segmenti brevi: restano una sessantina di battute di dialogo sotto i 10
   caratteri (`- Eh?`). Sono corrette e non vanno accorpate, ma sono il caso
   peggiore per Chatterbox: da tenere d'occhio nel QC. Non sono piu' falsi
   positivi automatici dell'euristica, vedi "Il flag sospetto".

**Igiene**

6. ~~`eccezioni.json` unico e globale~~: fatto, `-e` è ripetibile e i dizionari
   si fondono. Vedi "Dizionari di respelling".
7. Il trattino di dialogo negli EPUB italiani e' spesso un en dash. Non e'
   verificato come lo vocalizzi Chatterbox: da controllare in ascolto, e in
   caso togliere via `eccezioni.json`.

## Dizionari di respelling: perché più file e non uno

`-e` è ripetibile, i file si fondono nell'ordine dato e a parità di chiave vince
l'ultimo. Serve perché i respelling sono di due nature diverse: quelli che
valgono per la lingua (`"tempo": "tèmpo"`, un vocabolario che si scrive una
volta e si riusa) e quelli che valgono per un libro solo (nomi dei personaggi,
sigle). Tenerli in un file unico obbligherebbe a copiare il vocabolario grande
per ogni titolo. L'ultimo che vince permette al file del libro di correggere una
voce di quello generale senza toccarlo.

Due dettagli dell'applicazione, in `applica()`:

- **chiavi più lunghe per prime.** Fra `AD` e `ADHD` deve vincere la più
  specifica, non quella che capita prima nell'iterazione del dizionario.
- **chiave minuscola = anche a inizio frase.** `"tempo"` prende anche `Tempo` e
  restituisce `Tèmpo`. Senza questo, in un vocabolario di parole comuni si
  perderebbe un'occorrenza ogni poche righe. Le chiavi che contengono già una
  maiuscola restano esatte, altrimenti `ADHD` catturerebbe `adhd`.
- la sostituzione passa per una lambda invece che per la stringa di rimpiazzo:
  così backslash e `\1` nel valore restano letterali e non li interpreta `re.sub`.

Cambiare dizionari non invalida la cache di tutto: la firma copre il testo **dopo**
il respelling, quindi si rigenerano solo i segmenti che cambiano davvero.

## Il flag sospetto: perché aveva dei falsi positivi permanenti

L'euristica era `attesa = len(testo)/14` con finestra `[0.6, 1.8] * attesa`.
Sui testi corti la finestra è più stretta della variabilità fisiologica del TTS:
`- Eh?` ammetteva `[0.21, 0.64]` secondi, ma Chatterbox mette sempre attacco e
coda di silenzio e quella battuta esce sui 0.9s. Risultato: le sessanta battute
brevi erano sospette **per costruzione**, non uscivano mai dalla lista per
quante volte le rigenerassi, e facevano sembrare che il flag non si togliesse.

Ora `attesa = OVERHEAD + len(testo)/CPS` (0.4s + caratteri/14) con un margine
assoluto di 0.5s per parte e un pavimento di 0.25s sotto il quale è sospetto
comunque. Tutto in `valuta()`, una funzione sola usata sia in scrittura che da
`--solo-sospetti`.

Il flag **si toglie da solo**: il manifest è in append e vince l'ultima riga per
id, quindi una rigenerazione riuscita sovrascrive `sospetto: true`. Quello che
non si aggiorna da solo è il flag già scritto quando cambiano le soglie. Da qui
`--rivaluta`: ricalcola da testo e durata salvati nel manifest e riscrive solo i
flag cambiati, senza toccare la GPU. `--solo-sospetti` invece ricalcola sempre
al volo, così un cambio di euristica vale subito; `--rivaluta` serve perché
`assembla_m4b.py` legge il flag salvato.

Resta un'euristica sulla sola durata: vede i troncamenti, non le allucinazioni
a durata giusta. Il QC con Whisper resta da scrivere.

## Rigenerazione mirata: perché scavalca la cache

`--solo-id ch13_0207,ch07_0044` (o `@file`) e `--solo-sospetti` limitano la corsa
a quegli id **e ignorano la cache per loro**. Le due cose devono andare insieme:
se la cache restasse attiva il comando non farebbe niente, perché la firma di
quei segmenti non è cambiata.

È il punto: la firma copre testo più parametri, quindi intercetta il caso "il
testo era sbagliato" ma non il caso "la generazione è venuta male". Con
`temperature=0.6` la sintesi è stocastica e lo stesso identico input può uscire
bene o troncato. Quello serve poterlo ritirare, anche più volte di seguito.

Il ripiego senza questi flag era `-c N --force`, che rigenera l'intero capitolo:
oltre al tempo, ritira i dadi anche sui segmenti già buoni e può peggiorarli.
`-c N` da solo invece non fa nulla, perché il filtro per capitolo agisce prima
del controllo di cache.

## Assemblaggio M4B: com'e' stato risolto

`assembla_m4b.py` legge `manifest.jsonl`, concatena con il concat demuxer di
ffmpeg inserendo i silenzi (300 / 800 / 1200 ms), codifica in AAC mono a 24kHz
e scrive capitoli e metadata. Uso in `README.md`.

Quattro punti in cui l'implementazione si discosta dalla specifica iniziale, e
il perché:

- **`titolo` e `fine_paragrafo` non erano nel manifest.** La specifica li dava
  per presenti, ma `tts_chatterbox.py` non li scriveva. Ora li ricopia dal
  JSONL (non entrano nella `firma`, quindi non invalidano la cache). Per i
  manifest generati prima c'e' il fallback `--jsonl libro.jsonl`, e senza
  nessuna delle due fonti lo script si ferma con un messaggio esplicito.
- **Niente resampling.** I WAV escono gia' a 24kHz
  (`S3GEN_SR = 24000` in `chatterbox/models/s3gen/const.py`), quindi il
  "48kHz -> 24kHz" della specifica non aveva oggetto.
- **La tolleranza dell'1% si misura su parlato + pause.** Confrontare la durata
  del file con la sola somma delle `durata` del manifest darebbe sempre errore:
  su un libro intero i silenzi sono decine di minuti, molto oltre l'1%.
- **Copertina e metadata li estrae `assembla_m4b.py`, non `epub_to_jsonl.py`.**
  Con `--epub libro.epub` prende titolo e autore dal Dublin Core e la copertina
  da `ITEM_COVER`, con fallback su `<meta name="cover">` e poi su un'immagine
  che si chiami "cover". Cosi' il dato non deve attraversare due file
  intermedi. Restano sovrascrivibili a mano con `--titolo`, `--autore`,
  `--copertina`.

I segmenti `sospetto: true` non bloccano l'assemblaggio: vengono elencati a
fine corsa con id, durata e testo, per la revisione manuale.

**La codifica è tutta CPU, e va bene così.** La GPU accelera il video (NVENC),
non l'audio: un encoder AAC su GPU non esiste, e non servirebbe. AAC mono 24kHz
gira a circa 200x il tempo reale, misurato: 30 minuti di audio in 8.5 secondi,
quindi un libro da 12 ore sono un paio di minuti. Il collo di bottiglia di
`assembla_m4b.py` è la lettura di migliaia di WAV, non l'encoder. La GPU serve
solo al passo 2.

## Convenzioni di stile
Niente trattini lunghi nel codice generato o nei commenti (preferenza utente
in tutto il progetto). Commenti e messaggi CLI in italiano. Nomi di file e
variabili in inglese dove è idiomatico (es. `jsonl`, `wav`), altrove italiano.

## Cose da NON reintrodurre
- Backend `espeak` nel tokenizer di pykokoro: produce fonemi inglesi su testo
  italiano (bug verificato nel sorgente, non e' un problema di config).
- Kokoro nativo (`kokorog2p`) per l'italiano senza post processing: mette
  l'accento IPA dopo la vocale invece che prima, sposta lo stress di una
  sillaba. Serve il filtro fonemi discusso prima di usarlo.
- Chatterbox V1: non esiste una variante multilingue V1, solo inglese.

