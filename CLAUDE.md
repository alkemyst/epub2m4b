# CLAUDE.md

## Progetto
Pipeline EPUB -> audiolibro in italiano, TTS con Chatterbox Multilingual V3
(voice cloning), fallback Kokoro se Chatterbox risulta troppo instabile su
capitoli lunghi.

## Stato attuale
- `epub_to_jsonl.py`: EPUB -> JSONL di segmenti (funzionante, testato su EPUB reale).
  Flag `--lista-capitoli` (tabella dei capitoli, non scrive) e `--salta-capitoli 1-4,29,30`
  (esclude front e back matter). Riconosce le intestazioni numerate: `titolo` diventa
  "Capitolo 1" per l'M4B mentre il testo letto diventa "Capitolo uno.", perché dare `1.`
  da solo in pasto al TTS produce troncamenti.
- `tts_chatterbox.py`: JSONL -> WAV per segmento + manifest.jsonl riprendibile (funzionante)
- `eccezioni.json`: dizionario respelling (ADHD -> "addì accadì", ecc.), applicato prima della sintesi
- `README.md`: comandi per girare la conversione EPUB -> WAV
- Assemblaggio finale in M4B: **da scrivere**
- Controllo qualità automatico (Whisper su ogni segmento): **da scrivere**

### Scelta di progetto: gli id non si rinumerano
`--salta-capitoli` filtra **dopo** la numerazione, quindi escludendo i capitoli 1-4 il
primo capitolo narrato resta `ch05`. Gli id sono la chiave di cache di `manifest.jsonl`:
se slittassero, ritoccare la lista degli esclusi a sintesi avviata rigenererebbe ore di
audio già buono. La numerazione con buchi non si vede nel prodotto finale, perché
`assembla_m4b.py` ordina per `chapter` e l'etichetta visibile viene da `titolo`.

## Ambiente
- `venv-cb` (Python 3.11, uv), GPU GTX 1660 Ti 6GB, niente Tensor Core
- `setuptools<81` necessario per `pkg_resources` (dipendenza di `perth`)
- Chatterbox installato da GitHub (`t3_model="v3"` non è ancora su PyPI)
- Riferimento vocale: `ref_stefano_buendia.wav`, mono 48kHz, 6-10s, normalizzato con `loudnorm`

## Parametri Chatterbox di partenza
`language_id="it"`, `exaggeration=0.65`, `cfg_weight=0.5`, `temperature=0.6`,
`repetition_penalty=2.0`. Se ricompaiono troncamenti forzati (`forcing EOS
token` nel log), alzare `repetition_penalty` a 2.2-2.5.

Attenzione: `repetition_penalty` è documentato qui ma `tts_chatterbox.py` non lo
espone e non lo passa a `model.generate()`, quindi al momento quel rimedio non è
applicabile. Vedi "Cosa manca".

## Cosa manca per avere un audiolibro

La pipeline oggi arriva a "un WAV per segmento piu' `manifest.jsonl`". Da li' a un
M4B ascoltabile mancano queste cose, in ordine di quanto bloccano.

**Bloccanti**

1. `assembla_m4b.py`: non esiste. Specifica qui sotto.
2. Metadata del libro: `epub_to_jsonl.py` non estrae titolo e autore dall'EPUB,
   e non li scrive nel JSONL. Servono all'M4B. Da prendere da
   `libro.get_metadata("DC", "title")` e `"creator"`.
3. Copertina: non estratta. Sta nell'EPUB come `ITEM_COVER` o come `ITEM_IMAGE`
   referenziata nel manifest OPF. Va salvata su file per darla a ffmpeg.

**Qualità dell'audio**

4. Normalizzazione di loudness: nessuno step la applica. I segmenti escono a
   volume variabile e vanno uniformati prima o durante l'assemblaggio, con
   `loudnorm` a due passate. Target audiolibro: circa -19 LUFS mono.
5. `repetition_penalty` non e' esposto da `tts_chatterbox.py` ne' passato a
   `model.generate()`. E' il rimedio documentato contro i troncamenti forzati,
   quindi oggi quel consiglio non e' applicabile. Aggiungere il flag.
6. Rigenerazione mirata: si puo' filtrare per capitolo (`-c`) o per numero
   (`-n`), ma non per id. Per rifare i singoli segmenti marcati `sospetto`
   bisogna editare a mano il manifest. Serve un `--solo-id ch07_0123,...`.

**Controllo qualità**

7. QC con Whisper: trascrivere ogni WAV e confrontare con il testo di partenza
   (distanza di edit normalizzata). E' l'unico modo per beccare le allucinazioni
   e i troncamenti che la sola durata non vede. Il flag `sospetto` attuale e'
   solo un'euristica su durata attesa contro durata reale.
8. Segmenti brevi: restano una sessantina di battute di dialogo sotto i 10
   caratteri (`- Eh?`). Sono corrette e non vanno accorpate, ma sono il caso
   peggiore per Chatterbox: da tenere d'occhio nel QC.

**Igiene**

9. `eccezioni.json` e' unico e globale, mentre il respelling e' per libro
   (nomi propri, sigle). Andrebbe affiancato un file per titolo.
10. Il trattino di dialogo negli EPUB italiani e' spesso un en dash. Non e'
    verificato come lo vocalizzi Chatterbox: da controllare in ascolto, e in
    caso togliere via `eccezioni.json`.

## Prossimo passo: assemblaggio M4B

Scrivi `assembla_m4b.py` che:

1. Legge `manifest.jsonl` (prodotto da `tts_chatterbox.py`), ordinato per `id`.
2. Concatena i WAV con `ffmpeg concat demuxer`, inserendo silenzio tra
   segmenti: 300ms normale, 800ms se `fine_paragrafo=true` nel manifest,
   1200ms a cambio capitolo.
3. Codifica in AAC, mono, ~48-64kbps (voce, non serve di più). I WAV escono già
   a 24kHz (`S3GEN_SR = 24000` in `chatterbox/models/s3gen/const.py`), quindi non
   c'è nessun resampling da 48kHz da fare: tenere 24kHz e basta.
4. Genera capitoli FFMETADATA da `chapter` + `titolo` nel manifest, con
   timestamp calcolati dalla durata cumulativa dei segmenti.
5. Applica metadata ID3/M4B: titolo libro, autore, narratore ("Chatterbox IT,
   voce clonata"), copertina se presente nell'EPUB (`epub_to_jsonl.py` non la
   estrae ancora: aggiungere).
6. Verifica finale: durata totale del file contro somma delle durate nel
   manifest, tolleranza 1%. Se fuori tolleranza, segnala invece di procedere
   silenziosamente.

Segmenti con `sospetto: true` nel manifest: non bloccare l'assemblaggio, ma
stampare un riepilogo a fine corsa con id e testo, per revisione manuale
prima di considerare il libro finito.

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

