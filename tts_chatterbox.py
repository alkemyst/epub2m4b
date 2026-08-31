#!/usr/bin/env python3
"""Genera un WAV per segmento da un JSONL. Riprendibile."""
import json, hashlib, argparse, warnings, sys, re
from pathlib import Path
import torchaudio as ta
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

warnings.filterwarnings("ignore")

# Euristica sul sospetto: durata attesa contro durata reale. Una parte fissa
# (attacco, respiro, coda di silenzio che Chatterbox mette sempre) piu' una
# parte proporzionale al testo.
OVERHEAD = 0.4      # secondi di contorno, indipendenti dalla lunghezza
CPS = 14.0          # caratteri al secondo di parlato
MARGINE = 0.5       # tolleranza assoluta, in secondi
MINIMA = 0.25       # sotto questa durata e' sospetto comunque

def valuta(testo: str, durata: float) -> bool:
    """True se la durata non torna con la lunghezza del testo.

    La parte fissa e il margine assoluto servono ai segmenti brevi. Con la sola
    proporzione, "- Eh?" ha una finestra di [0.21, 0.64] secondi: nessuna
    generazione reale ci sta dentro, quindi resterebbe sospetto per sempre
    qualunque cosa esca dal TTS, e ogni --solo-sospetti se lo ritroverebbe.

    Resta un'euristica: vede i troncamenti grossi, non le allucinazioni a
    durata giusta. Per quelle serve il QC con Whisper, ancora da scrivere.
    """
    attesa = OVERHEAD + len(testo) / CPS
    return (durata < max(MINIMA, attesa * 0.6 - MARGINE)
            or durata > attesa * 1.8 + MARGINE)

def carica_eccezioni(percorsi: list, obbligatori: bool) -> dict:
    """Fonde piu' dizionari di respelling nell'ordine dato.

    A parita' di chiave vince l'ultimo file, cosi' si puo' tenere un vocabolario
    generale (gli accenti delle parole comuni) e sovrascriverne qualche voce con
    un file specifico del libro, senza copiare regole da un file all'altro.

    obbligatori=False vale per il solo default: se eccezioni.json non c'e' si
    procede senza respelling invece di fermarsi.
    """
    ecc = {}
    for p in percorsi:
        if not p.exists():
            if obbligatori:
                sys.exit(f"errore: dizionario non trovato: {p}")
            continue
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            sys.exit(f"errore: {p} non e' JSON valido: {e}")
        if not isinstance(d, dict):
            sys.exit(f"errore: {p} deve contenere un oggetto, "
                     f"non {type(d).__name__}")
        doppie = sorted(set(d) & set(ecc))
        print(f"  {p}: {len(d)} regol{'a' if len(d) == 1 else 'e'}"
              + (f", {len(doppie)} sovrascriv"
                 f"{'e' if len(doppie) == 1 else 'ono'} le precedenti"
                 if doppie else ""))
        if doppie:
            print("    " + ", ".join(doppie[:8]) + (" ..." if len(doppie) > 8 else ""))
        ecc.update(d)
    return ecc

def maiuscola(s: str) -> str:
    """Come str.capitalize() ma senza abbassare il resto della stringa."""
    return s[:1].upper() + s[1:]

def applica(testo: str, ecc: dict) -> str:
    """Applica il respelling sul confine di parola.

    Due dettagli che contano con un vocabolario grande:

    - le chiavi piu' lunghe vanno per prime, cosi' fra due regole che si
      sovrappongono vince la piu' specifica e non quella che capita prima;
    - una chiave tutta minuscola vale anche a inizio frase, dove la parola e'
      maiuscola. Senza questo, in un vocabolario di parole comuni verrebbe
      saltata una occorrenza ogni poche righe. Le chiavi che contengono gia'
      una maiuscola (ADHD, Macchiavelli) restano invece esatte.
    """
    for k in sorted(ecc, key=len, reverse=True):
        v = ecc[k]
        # sostituzione via lambda: cosi' backslash e \1 nel valore restano
        # letterali invece di essere interpretati da re.sub
        testo = re.sub(rf"\b{re.escape(k)}\b", lambda _m, v=v: v, testo)
        if k.islower():
            testo = re.sub(rf"\b{re.escape(maiuscola(k))}\b",
                           lambda _m, v=maiuscola(v): v, testo)
    return testo

def carica_ids(spec: str) -> set:
    """'ch05_0001,ch07_0044' oppure '@file.txt'.

    Nel file gli id possono stare uno per riga o separati da virgole, e il
    cancelletto commenta fino a fine riga: cosi' si puo' incollare la lista dei
    sospetti stampata da assembla_m4b.py e annotarla.
    """
    if spec.startswith("@"):
        p = Path(spec[1:])
        if not p.exists():
            sys.exit(f"errore: file non trovato: {p}")
        spec = p.read_text()
    ids = set()
    for riga in spec.splitlines():
        for pezzo in riga.split("#", 1)[0].replace(",", " ").split():
            ids.add(pezzo)
    return ids

def firma(testo: str, cfg: dict) -> str:
    """Cambia se cambia il testo o un parametro: invalida la cache."""
    blob = testo + json.dumps(cfg, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]

def main():
    p = argparse.ArgumentParser()
    p.add_argument("jsonl", type=Path)
    p.add_argument("-o", "--outdir", type=Path, default=Path("audio"))
    p.add_argument("-r", "--ref", default="references/ref_stefano_buendia.wav")
    p.add_argument("-e", "--eccezioni", type=Path, action="append", default=None,
                   help="dizionario di respelling, ripetibile: i file si "
                        "fondono nell'ordine e l'ultimo vince sui doppioni "
                        "(default: eccezioni.json)")
    p.add_argument("--senza-eccezioni", action="store_true",
                   help="nessun respelling, ignora anche il default")
    p.add_argument("--exaggeration", type=float, default=0.65)
    p.add_argument("--cfg-weight", type=float, default=0.5)
    p.add_argument("--temperature", type=float, default=0.6)
    p.add_argument("--force", action="store_true")
    p.add_argument("-c", "--chapter", type=int, default=None,
                   help="genera solo questo capitolo")
    p.add_argument("-n", "--limite", type=int, default=None,
                   help="genera al massimo N segmenti")
    p.add_argument("--solo-id", default=None,
                   help="rigenera solo questi id ignorando la cache: lista "
                        "separata da virgole, oppure @file")
    p.add_argument("--solo-sospetti", action="store_true",
                   help="rigenera gli id sospetti secondo il manifest")
    p.add_argument("--rivaluta", action="store_true",
                   help="ricalcola il flag sospetto sull'audio gia' prodotto e "
                        "aggiorna il manifest, senza rigenerare niente")
    args = p.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    if args.senza_eccezioni:
        if args.eccezioni:
            sys.exit("errore: --senza-eccezioni e -e si escludono a vicenda")
        ecc = {}
        print("respelling disattivato")
    else:
        # senza -e vale il default, e se manca si tira dritto senza respelling;
        # con -e i file sono stati chiesti esplicitamente, quindi devono esserci
        espliciti = bool(args.eccezioni)
        percorsi = args.eccezioni if espliciti else [Path("eccezioni.json")]
        print("dizionari di respelling:")
        ecc = carica_eccezioni(percorsi, espliciti)
        if not ecc:
            print("  nessuno, il testo va in sintesi cosi' com'e'")
        elif len(percorsi) > 1:
            print(f"  totale: {len(ecc)} regole")

    cfg = {
        "exaggeration": args.exaggeration,
        "cfg_weight": args.cfg_weight,
        "temperature": args.temperature,
        "ref": args.ref,
        "modello": "v3",
    }

    segmenti = [json.loads(l) for l in args.jsonl.read_text().splitlines() if l.strip()]
    if args.chapter is not None:
         segmenti = [s for s in segmenti if s.get("chapter") == args.chapter]

    manifest_path = args.outdir / "manifest.jsonl"
    fatti = {}
    if manifest_path.exists():
        for l in manifest_path.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                fatti[r["id"]] = r        # ultima riga per id: le rigenerazioni vincono

    if args.rivaluta:
        if not manifest_path.exists():
            sys.exit(f"errore: manifest non trovato: {manifest_path}\n"
                     "--rivaluta lavora sui risultati di una corsa precedente")
        # Il flag scritto nel manifest e' quello calcolato dalla corsa che ha
        # generato il segmento: se le soglie cambiano, resta indietro. Qui lo
        # ricalcoliamo da testo e durata gia' salvati, senza toccare l'audio.
        cambiate = [r for r in fatti.values()
                    if bool(r.get("sospetto")) != valuta(r["text"], r["durata"])]
        if not cambiate:
            print(f"{len(fatti)} segmenti nel manifest, nessun flag da correggere")
            return
        with manifest_path.open("a") as mf:
            for r in cambiate:
                r["sospetto"] = valuta(r["text"], r["durata"])
                mf.write(json.dumps(r, ensure_ascii=False) + "\n")
        promossi = sum(1 for r in cambiate if r["sospetto"])
        print(f"{len(fatti)} segmenti nel manifest, {len(cambiate)} flag corretti: "
              f"{len(cambiate) - promossi} non piu' sospetti, {promossi} ora sospetti")
        return

    selezione = carica_ids(args.solo_id) if args.solo_id else set()
    if args.solo_sospetti:
        if not manifest_path.exists():
            sys.exit(f"errore: manifest non trovato: {manifest_path}\n"
                     "--solo-sospetti lavora sui risultati di una corsa precedente")
        # non ci fidiamo del flag salvato: lo ricalcoliamo con le soglie di
        # adesso, cosi' un cambio di euristica vale subito senza --rivaluta
        sospetti = {i for i, r in fatti.items() if valuta(r["text"], r["durata"])}
        if not sospetti:
            sys.exit(f"errore: nessun segmento sospetto in {manifest_path}")
        print(f"--solo-sospetti: {len(sospetti)} id presi dal manifest")
        selezione |= sospetti

    if selezione:
        ignoti = sorted(selezione - {s["id"] for s in segmenti})
        if ignoti:
            dove = f" o fuori dal capitolo {args.chapter}" if args.chapter else ""
            print(f"attenzione: {len(ignoti)} id non presenti nel JSONL{dove}: "
                  + ", ".join(ignoti[:10]) + (" ..." if len(ignoti) > 10 else ""))
        segmenti = [s for s in segmenti if s["id"] in selezione]
        if not segmenti:
            sys.exit("errore: nessun id della selezione corrisponde a un segmento")

    # Con una selezione esplicita la cache va scavalcata. La firma non e'
    # cambiata (il testo e i parametri sono gli stessi), ma e' proprio quello
    # che si vuole: ritirare i dadi su una generazione venuta male.
    ignora_cache = args.force or bool(selezione)

    da_fare = [
        s for s in segmenti
        if ignora_cache
        or s["id"] not in fatti
        or fatti[s["id"]]["firma"] != firma(applica(s["text"], ecc), cfg)
    ]
    if args.limite:
        da_fare = da_fare[:args.limite]
    if selezione:
        print(f"{len(segmenti)} segmenti selezionati, {len(da_fare)} da "
              "rigenerare (cache ignorata)")
    else:
        print(f"{len(segmenti)} segmenti, {len(da_fare)} da generare")
    if not da_fare:
        return

    model = ChatterboxMultilingualTTS.from_pretrained(device="cuda", t3_model="v3")

    with manifest_path.open("a") as mf:
        for i, s in enumerate(da_fare, 1):
            testo = applica(s["text"], ecc)
            wav = model.generate(
                testo,
                language_id="it",
                audio_prompt_path=args.ref,
                exaggeration=args.exaggeration,
                cfg_weight=args.cfg_weight,
                temperature=args.temperature,
            )
            out = args.outdir / f"{s['id']}.wav"
            ta.save(str(out), wav, model.sr)

            durata = wav.shape[-1] / model.sr
            sospetto = valuta(testo, durata)

            mf.write(json.dumps({
                "id": s["id"],
                "chapter": s.get("chapter"),
                # titolo e fine_paragrafo servono ad assembla_m4b.py per i
                # capitoli e per la durata delle pause: li ricopiamo qui cosi'
                # non serve rileggere il JSONL di partenza
                "titolo": s.get("titolo"),
                "fine_paragrafo": s.get("fine_paragrafo", False),
                "file": out.name,
                "text": testo,
                "durata": round(durata, 2),
                "firma": firma(testo, cfg),
                "sospetto": sospetto,
            }, ensure_ascii=False) + "\n")
            mf.flush()

            print(f"[{i}/{len(da_fare)}] {s['id']} {durata:.1f}s"
                  + ("  <-- CONTROLLA" if sospetto else ""))

if __name__ == "__main__":
    main()

