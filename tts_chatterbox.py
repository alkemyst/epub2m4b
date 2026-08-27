#!/usr/bin/env python3
"""Genera un WAV per segmento da un JSONL. Riprendibile."""
import json, hashlib, argparse, warnings, sys
from pathlib import Path
import torchaudio as ta
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

warnings.filterwarnings("ignore")

def carica_eccezioni(path: Path) -> dict:
    return json.loads(path.read_text()) if path and path.exists() else {}

def applica(testo: str, ecc: dict) -> str:
    import re
    for k, v in ecc.items():
        testo = re.sub(rf"\b{re.escape(k)}\b", v, testo)
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
    p.add_argument("-e", "--eccezioni", type=Path, default=Path("eccezioni.json"))
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
                   help="rigenera gli id marcati sospetto nel manifest")
    args = p.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    ecc = carica_eccezioni(args.eccezioni)

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

    selezione = carica_ids(args.solo_id) if args.solo_id else set()
    if args.solo_sospetti:
        if not manifest_path.exists():
            sys.exit(f"errore: manifest non trovato: {manifest_path}\n"
                     "--solo-sospetti lavora sui risultati di una corsa precedente")
        sospetti = {i for i, r in fatti.items() if r.get("sospetto")}
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
            attesa = len(testo) / 14.0          # circa 14 caratteri al secondo
            sospetto = durata < attesa * 0.6 or durata > attesa * 1.8

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

