#!/usr/bin/env python3
"""Ritaglia e normalizza una voce di riferimento per il voice cloning."""
import json, argparse, subprocess, shutil, sys, tempfile
from pathlib import Path

# Chatterbox non usa il riferimento tutto allo stesso modo:
#   ENC_COND_LEN = 6 * 16000   -> i token di prompt guardano i primi 6 secondi
#   DEC_COND_LEN = 10 * 24000  -> il decoder guarda i primi 10 secondi
#   l'embedding di speaker invece legge il file intero
# Da qui la regola dei 6-10 secondi, e il fatto che i primi 6 contano di piu'.
UTILI_PROMPT = 6.0
UTILI_DECODER = 10.0

# Il target di loudness non e' critico per il riferimento, ma tenerlo uguale
# fra i candidati serve a confrontarli a orecchio senza che vinca il piu' forte.
TARGET_I = -19.0
TARGET_TP = -1.5
TARGET_LRA = 7.0

def esegui(cmd: list, descrizione: str) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        coda = r.stderr.strip().splitlines()[-15:]
        sys.exit(f"errore: {descrizione}\n" + "\n".join(coda))
    return r.stderr

def misura(path: Path) -> dict:
    """Prima passata di loudnorm: torna i valori misurati."""
    err = esegui(["ffmpeg", "-nostats", "-i", str(path),
                  "-af", f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
                         ":print_format=json", "-f", "null", "-"],
                 f"misura di {path.name}")
    blocco = err[err.rindex("{"):err.rindex("}") + 1]
    return json.loads(blocco)

def durata(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True)
    return float(out.stdout.strip())

def main():
    p = argparse.ArgumentParser(
        description="Da una registrazione qualsiasi a un riferimento vocale "
                    "mono 48kHz normalizzato.")
    p.add_argument("sorgente", type=Path,
                   help="audio o video di partenza, qualsiasi formato")
    p.add_argument("-o", "--out", type=Path, required=True)
    p.add_argument("--da", default="0",
                   help="istante di inizio, es. 90 oppure 1:30.5")
    p.add_argument("--durata", type=float, default=8.0,
                   help="secondi da ritagliare (default 8)")
    args = p.parse_args()

    for strumento in ("ffmpeg", "ffprobe"):
        if shutil.which(strumento) is None:
            sys.exit(f"errore: {strumento} non trovato nel PATH")
    if not args.sorgente.exists():
        sys.exit(f"errore: sorgente non trovata: {args.sorgente}")

    if args.durata < UTILI_PROMPT:
        print(f"attenzione: {args.durata}s sono meno dei {UTILI_PROMPT:.0f}s che "
              "Chatterbox usa per il prompt, la voce sara' meno caratterizzata")
    elif args.durata > UTILI_DECODER:
        print(f"attenzione: oltre i {UTILI_DECODER:.0f}s solo l'embedding di "
              "speaker legge l'audio in piu', il resto lo ignora")

    with tempfile.TemporaryDirectory() as td:
        taglio = Path(td) / "taglio.wav"
        # il ritaglio va fatto prima di misurare, altrimenti si normalizza
        # sulla loudness di tutta la registrazione invece che su quella del pezzo
        esegui(["ffmpeg", "-v", "error", "-y", "-ss", args.da,
                "-t", f"{args.durata}", "-i", str(args.sorgente),
                "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le",
                str(taglio)], "ritaglio")

        reale = durata(taglio)
        if reale < args.durata - 0.05:
            print(f"attenzione: ritagliati {reale:.2f}s invece di {args.durata}s, "
                  "la sorgente finisce prima")

        m = misura(taglio)
        print(f"prima:  I={m['input_i']} LUFS  TP={m['input_tp']} dBTP  "
              f"LRA={m['input_lra']}")

        args.out.parent.mkdir(parents=True, exist_ok=True)
        esegui(["ffmpeg", "-v", "error", "-y", "-i", str(taglio),
                "-af", f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
                       f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
                       f":measured_LRA={m['input_lra']}"
                       f":measured_thresh={m['input_thresh']}"
                       f":offset={m['target_offset']}:linear=true",
                "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le",
                str(args.out)], "normalizzazione")

    d = misura(args.out)
    print(f"dopo:   I={d['input_i']} LUFS  TP={d['input_tp']} dBTP  "
          f"LRA={d['input_lra']}")
    print(f"scritto: {args.out}  ({durata(args.out):.2f}s, mono 48kHz)")
    print(f"\nprovalo cosi':\n  python tts_chatterbox.py libro.jsonl "
          f"-o /tmp/prova_voce -r {args.out} -n 5")

if __name__ == "__main__":
    main()
