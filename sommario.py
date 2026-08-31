#!/usr/bin/env python3
"""Struttura di un audiolibro: quanto dura ogni capitolo.

Legge un M4B gia' fatto (i capitoli via ffprobe) oppure un manifest.jsonl
prima di codificare. Il manifest dice qualcosa in piu': quanti segmenti e
quanti sospetti per capitolo.
"""
import json, argparse, subprocess, shutil, sys
from pathlib import Path

# stesse pause di assembla_m4b.py: senza, il totale del manifest non torna
# con quello del file finito
PAUSA_NORMALE = 300
PAUSA_PARAGRAFO = 800
PAUSA_CAPITOLO = 1200

def hms(sec: float) -> str:
    sec = int(round(sec))
    h, resto = divmod(sec, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def da_m4b(path: Path) -> tuple[list[dict], dict]:
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                        "-show_chapters", "-show_format", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"errore: ffprobe non legge {path}\n" + r.stderr.strip())
    d = json.loads(r.stdout)
    if not d.get("chapters"):
        sys.exit(f"errore: {path} non ha capitoli.\n"
                 "Se l'hai assemblato tu, controlla che ffmpeg abbia accettato "
                 "il file di FFMETADATA.")
    capitoli = []
    for i, c in enumerate(d["chapters"], 1):
        inizio, fine = float(c["start_time"]), float(c["end_time"])
        capitoli.append({"n": i,
                         "titolo": c.get("tags", {}).get("title") or "",
                         "inizio": inizio, "durata": fine - inizio})
    f = d.get("format", {})
    tag = {k.lower(): v for k, v in (f.get("tags") or {}).items()}
    return capitoli, {"titolo": tag.get("title"), "autore": tag.get("artist"),
                      "durata": float(f.get("duration", 0)),
                      "byte": int(f.get("size", 0))}

def da_manifest(path: Path) -> tuple[list[dict], dict]:
    per_id = {}
    for l in path.read_text().splitlines():
        if l.strip():
            r = json.loads(l)
            per_id[r["id"]] = r          # append: vince l'ultima riga
    def chiave(r):
        cap, seq = r["id"].split("_")
        return (r.get("chapter") or int(cap[2:]), int(seq))
    righe = sorted(per_id.values(), key=chiave)

    capitoli, corrente = [], None
    for i, r in enumerate(righe):
        if corrente is None or r.get("chapter") != corrente["n"]:
            corrente = {"n": r.get("chapter"), "titolo": r.get("titolo") or "",
                        "inizio": 0.0, "durata": 0.0, "parlato": 0.0,
                        "segmenti": 0, "sospetti": 0}
            capitoli.append(corrente)
        if not corrente["titolo"] and r.get("titolo"):
            corrente["titolo"] = r["titolo"]
        corrente["parlato"] += r["durata"]
        corrente["segmenti"] += 1
        corrente["sospetti"] += bool(r.get("sospetto"))

        successiva = righe[i + 1] if i + 1 < len(righe) else None
        if successiva is None:
            pausa = 0
        elif successiva.get("chapter") != r.get("chapter"):
            pausa = PAUSA_CAPITOLO
        else:
            pausa = PAUSA_PARAGRAFO if r.get("fine_paragrafo") else PAUSA_NORMALE
        corrente["durata"] += r["durata"] + pausa / 1000

    cursore = 0.0
    for c in capitoli:
        c["inizio"] = cursore
        cursore += c["durata"]
    return capitoli, {"durata": cursore, "segmenti": len(righe),
                      "parlato": sum(c["parlato"] for c in capitoli),
                      "sospetti": sum(c["sospetti"] for c in capitoli)}

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sorgente", type=Path, help="libro.m4b oppure manifest.jsonl")
    p.add_argument("--csv", action="store_true",
                   help="una riga per capitolo, separata da punto e virgola")
    args = p.parse_args()

    if not args.sorgente.exists():
        sys.exit(f"errore: sorgente non trovata: {args.sorgente}")
    manifest = args.sorgente.suffix == ".jsonl"
    if not manifest and shutil.which("ffprobe") is None:
        sys.exit("errore: ffprobe non trovato nel PATH")

    capitoli, meta = (da_manifest if manifest else da_m4b)(args.sorgente)

    if args.csv:
        print("capitolo;titolo;inizio_s;durata_s")
        for c in capitoli:
            titolo = c["titolo"].replace(";", ",")
            print(f"{c['n']};{titolo};{c['inizio']:.1f};{c['durata']:.1f}")
        return

    # la mediana e' piu' robusta della media: bastano un paio di capitoli
    # sbagliati per spostare la media e non far notare piu' niente
    durate = sorted(c["durata"] for c in capitoli)
    mediana = durate[len(durate) // 2]

    larg = max([len(c["titolo"]) for c in capitoli] + [7])
    testa = f"{'cap':>4}  {'titolo':<{larg}}  {'inizio':>8}  {'durata':>7}  {'%':>5}"
    if manifest:
        testa += f"  {'segm':>5}  {'sosp':>5}"
    print(testa)
    print("-" * len(testa))
    for c in capitoli:
        quota = 100 * c["durata"] / meta["durata"] if meta["durata"] else 0
        riga = (f"{c['n']:>4}  {c['titolo']:<{larg}}  {hms(c['inizio']):>8}  "
                f"{hms(c['durata']):>7}  {quota:>4.1f}%")
        if manifest:
            riga += f"  {c['segmenti']:>5}  {c['sospetti'] or '':>5}"
        # un capitolo molto fuori dalla mediana di solito non e' uno stile
        # narrativo: e' front matter finito dentro, o sintesi interrotta
        if c["durata"] < mediana * 0.35:
            riga += "  <-- corto"
        elif c["durata"] > mediana * 2.5:
            riga += "  <-- lungo"
        print(riga)

    print("-" * len(testa))
    print(f"{len(capitoli)} capitoli, totale {hms(meta['durata'])} "
          f"({meta['durata'] / 3600:.2f} h), mediana {hms(mediana)}")
    if manifest:
        pause = meta["durata"] - meta["parlato"]
        print(f"{meta['segmenti']} segmenti, parlato {hms(meta['parlato'])} "
              f"+ pause {hms(pause)}")
        if meta["sospetti"]:
            print(f"{meta['sospetti']} segmenti sospetti, "
                  f"{100 * meta['sospetti'] / meta['segmenti']:.1f}% del totale")
        print("durata stimata dal manifest, il file finito puo' variare di poco")
    else:
        if meta.get("titolo"):
            print(f"titolo: {meta['titolo']}")
        if meta.get("autore"):
            print(f"autore: {meta['autore']}")
        if meta.get("byte"):
            kbps = meta["byte"] * 8 / meta["durata"] / 1000 if meta["durata"] else 0
            print(f"{meta['byte'] / 2**20:.0f} MiB, {kbps:.0f} kbps medi")

if __name__ == "__main__":
    main()
