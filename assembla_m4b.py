#!/usr/bin/env python3
"""manifest.jsonl + WAV per segmento -> audiolibro M4B con capitoli."""
import json, argparse, subprocess, shutil, sys, tempfile, warnings
from pathlib import Path

warnings.filterwarnings("ignore")

PAUSA_NORMALE = 300      # ms fra due segmenti dello stesso paragrafo
PAUSA_PARAGRAFO = 800    # ms dopo un segmento con fine_paragrafo
PAUSA_CAPITOLO = 1200    # ms a cambio capitolo

def esegui(cmd: list, descrizione: str) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        coda = r.stderr.strip().splitlines()[-15:]
        sys.exit(f"errore: {descrizione}\n" + "\n".join(coda))
    return r.stdout.strip()

def probe(path: Path, campi: str) -> list[str]:
    out = esegui(["ffprobe", "-v", "error", "-select_streams", "a:0",
                  "-show_entries", campi, "-of", "default=nw=1:nk=1", str(path)],
                 f"ffprobe su {path.name}")
    return out.splitlines()

def durata_file(path: Path) -> float:
    out = esegui(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=nw=1:nk=1", str(path)],
                 f"durata di {path.name}")
    return float(out)

def carica_manifest(path: Path) -> list[dict]:
    """Ultima riga per id: il manifest e' in append, le rigenerazioni vincono."""
    per_id = {}
    for l in path.read_text().splitlines():
        if l.strip():
            r = json.loads(l)
            per_id[r["id"]] = r
    def chiave(r):
        # niente sort lessicografico sull'id: con piu' di 99 capitoli
        # 'ch100_0001' finirebbe prima di 'ch99_0001'
        cap, seq = r["id"].split("_")
        return (r.get("chapter") or int(cap[2:]), int(seq))
    return sorted(per_id.values(), key=chiave)

def arricchisci(righe: list[dict], jsonl: Path | None) -> None:
    """Porta titolo e fine_paragrafo nelle righe del manifest.

    tts_chatterbox.py non li scrive, quindi vanno ripresi dal JSONL di
    partenza. Se un domani finiranno nel manifest, quelli hanno la precedenza.
    """
    mancano = [r for r in righe if "fine_paragrafo" not in r or "titolo" not in r]
    if not mancano:
        return
    if jsonl is None:
        sys.exit("errore: il manifest non ha 'titolo' e 'fine_paragrafo'.\n"
                 "Passa il JSONL di partenza con --jsonl libro.jsonl")
    origine = {}
    for l in jsonl.read_text().splitlines():
        if l.strip():
            s = json.loads(l)
            origine[s["id"]] = s
    persi = 0
    for r in righe:
        s = origine.get(r["id"])
        if s is None:
            persi += 1
            continue
        r.setdefault("titolo", s.get("titolo"))
        r.setdefault("fine_paragrafo", s.get("fine_paragrafo", False))
    if persi:
        print(f"attenzione: {persi} segmenti del manifest non sono nel JSONL, "
              "trattati come meta' paragrafo")
    for r in righe:
        r.setdefault("titolo", None)
        r.setdefault("fine_paragrafo", False)

def crea_silenzi(tmp: Path, campione: Path) -> dict[int, Path]:
    """Silenzi nello stesso identico formato dei segmenti.

    Il concat demuxer non converte niente: se codec, sample rate o canali non
    combaciano, l'audio esce sporco o ffmpeg si ferma.
    """
    codec, sr, canali = probe(campione, "stream=codec_name,sample_rate,channels")
    layout = {"1": "mono", "2": "stereo"}.get(canali, canali)
    fatti = {}
    for ms in (PAUSA_NORMALE, PAUSA_PARAGRAFO, PAUSA_CAPITOLO):
        out = tmp / f"silenzio_{ms}.wav"
        esegui(["ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl={layout}",
                "-t", f"{ms / 1000:.3f}", "-c:a", codec, str(out)],
               f"creazione del silenzio da {ms}ms")
        fatti[ms] = out
    return fatti

def pausa_dopo(riga: dict, successiva: dict | None) -> int:
    if successiva is None:
        return 0
    if successiva.get("chapter") != riga.get("chapter"):
        return PAUSA_CAPITOLO
    return PAUSA_PARAGRAFO if riga.get("fine_paragrafo") else PAUSA_NORMALE

def costruisci(righe: list[dict], audiodir: Path, silenzi: dict[int, Path],
               lista: Path) -> tuple[list[dict], int]:
    """Scrive la lista per il concat demuxer e calcola i confini dei capitoli.

    Torna (capitoli, durata_totale_ms). I capitoli sono in millisecondi, con la
    pausa di stacco conteggiata in coda al capitolo che si chiude, cosi' non
    restano buchi fra un capitolo e il successivo.
    """
    capitoli, cursore = [], 0
    corrente = None
    with lista.open("w") as f:
        for i, r in enumerate(righe):
            wav = audiodir / r["file"]
            if not wav.exists():
                sys.exit(f"errore: manca il file {wav}")
            if corrente is None or r.get("chapter") != corrente["chapter"]:
                if corrente is not None:
                    corrente["fine"] = cursore
                corrente = {"chapter": r.get("chapter"),
                            "titolo": r.get("titolo"),
                            "inizio": cursore}
                capitoli.append(corrente)
            if corrente["titolo"] is None and r.get("titolo"):
                corrente["titolo"] = r["titolo"]

            f.write("file '%s'\n" % str(wav.resolve()).replace("'", r"'\''"))
            cursore += int(round(r["durata"] * 1000))

            ms = pausa_dopo(r, righe[i + 1] if i + 1 < len(righe) else None)
            if ms:
                f.write("file '%s'\n" % str(silenzi[ms].resolve()))
                cursore += ms
    if corrente is not None:
        corrente["fine"] = cursore
    return capitoli, cursore

def scrivi_ffmetadata(path: Path, capitoli: list[dict], meta: dict) -> None:
    righe = [";FFMETADATA1"]
    for chiave, valore in meta.items():
        if valore:
            # in FFMETADATA vanno protetti = ; # e newline
            v = str(valore)
            for c in ("\\", "=", ";", "#", "\n"):
                v = v.replace(c, "\\" + c)
            righe.append(f"{chiave}={v}")
    for c in capitoli:
        titolo = c["titolo"] or f"Capitolo {c['chapter']}"
        righe += ["", "[CHAPTER]", "TIMEBASE=1/1000",
                  f"START={c['inizio']}", f"END={c['fine']}",
                  f"title={titolo}"]
    path.write_text("\n".join(righe) + "\n")

def dati_epub(epub_path: Path, tmp: Path) -> dict:
    """Titolo, autore e copertina dall'EPUB. Nessuno dei tre e' obbligatorio."""
    from ebooklib import epub, ITEM_COVER, ITEM_IMAGE
    libro = epub.read_epub(str(epub_path))
    def primo(campo):
        v = libro.get_metadata("DC", campo)
        return v[0][0] if v else None

    copertina = None
    candidati = list(libro.get_items_of_type(ITEM_COVER))
    if not candidati:
        # molti EPUB dichiarano la copertina come <meta name="cover" content="id">
        for _, attr in libro.get_metadata("OPF", "cover") or []:
            item = libro.get_item_with_id(attr.get("content", ""))
            if item is not None:
                candidati = [item]
                break
    if not candidati:
        candidati = [i for i in libro.get_items_of_type(ITEM_IMAGE)
                     if "cover" in i.get_name().lower()]
    if candidati:
        item = candidati[0]
        est = Path(item.get_name()).suffix or ".jpg"
        copertina = tmp / f"copertina{est}"
        copertina.write_bytes(item.get_content())

    return {"titolo": primo("title"), "autore": primo("creator"),
            "copertina": copertina}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("manifest", type=Path)
    p.add_argument("-o", "--out", type=Path, default=Path("audiolibro.m4b"))
    p.add_argument("-j", "--jsonl", type=Path, default=None,
                   help="JSONL di partenza, per titolo e fine_paragrafo")
    p.add_argument("--epub", type=Path, default=None,
                   help="da cui prendere titolo, autore e copertina")
    p.add_argument("--titolo", default=None)
    p.add_argument("--autore", default=None)
    p.add_argument("--copertina", type=Path, default=None)
    p.add_argument("--narratore", default="Chatterbox IT, voce clonata")
    p.add_argument("--bitrate", default="64k")
    p.add_argument("--dry-run", action="store_true",
                   help="prepara lista e capitoli senza codificare")
    args = p.parse_args()

    for strumento in ("ffmpeg", "ffprobe"):
        if shutil.which(strumento) is None:
            sys.exit(f"errore: {strumento} non trovato nel PATH")

    audiodir = args.manifest.parent
    righe = carica_manifest(args.manifest)
    if not righe:
        sys.exit("errore: manifest vuoto")
    arricchisci(righe, args.jsonl)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        da_epub = dati_epub(args.epub, tmp) if args.epub else {}
        titolo = args.titolo or da_epub.get("titolo") or args.out.stem
        autore = args.autore or da_epub.get("autore")
        copertina = args.copertina or da_epub.get("copertina")
        if copertina and not Path(copertina).exists():
            sys.exit(f"errore: copertina non trovata: {copertina}")

        silenzi = crea_silenzi(tmp, audiodir / righe[0]["file"])
        lista = tmp / "concat.txt"
        capitoli, totale_ms = costruisci(righe, audiodir, silenzi, lista)
        meta_path = tmp / "meta.txt"
        scrivi_ffmetadata(meta_path, capitoli, {
            "title": titolo, "album": titolo,
            "artist": autore, "album_artist": autore,
            "composer": args.narratore, "comment": f"Narrato da {args.narratore}",
            "genre": "Audiobook", "media_type": "2",
        })

        somma = sum(r["durata"] for r in righe)
        pause = totale_ms / 1000 - somma
        print(f"{len(righe)} segmenti, {len(capitoli)} capitoli")
        print(f"parlato {somma / 3600:.2f} h + pause {pause / 60:.1f} min "
              f"= {totale_ms / 3_600_000:.2f} h attese")
        print(f"titolo: {titolo}")
        print(f"autore: {autore or 'non impostato'}")
        print(f"copertina: {copertina if copertina else 'nessuna'}")

        if args.dry_run:
            for c in capitoli:
                print(f"  cap {c['chapter']:>3}  {c['inizio'] / 1000:9.1f}s  "
                      f"{c['titolo'] or ''}")
            print("dry run: non ho codificato niente")
            return

        cmd = ["ffmpeg", "-v", "error", "-stats", "-y",
               "-f", "concat", "-safe", "0", "-i", str(lista),
               "-i", str(meta_path)]
        if copertina:
            cmd += ["-i", str(copertina)]
        cmd += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        if copertina:
            cmd += ["-map", "2:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
        cmd += ["-c:a", "aac", "-b:a", args.bitrate, "-ac", "1",
                "-movflags", "+faststart", "-f", "ipod", str(args.out)]
        print("codifica in corso, ci vuole un po'...")
        esegui(cmd, "codifica M4B")

        # verifica: il confronto e' con parlato + pause, non con il solo parlato,
        # perche' i silenzi inseriti sono decine di minuti su un libro intero
        reale = durata_file(args.out)
        atteso = totale_ms / 1000
        scarto = abs(reale - atteso) / atteso * 100
        print(f"durata attesa {atteso / 3600:.2f} h, "
              f"reale {reale / 3600:.2f} h, scarto {scarto:.2f}%")
        if scarto > 1.0:
            print("ATTENZIONE: scarto oltre l'1%, il file potrebbe essere "
                  "incompleto. Controlla prima di considerarlo finito.")

        sospetti = [r for r in righe if r.get("sospetto")]
        if sospetti:
            print(f"\n{len(sospetti)} segmenti sospetti da riascoltare:")
            for r in sospetti:
                print(f"  {r['id']}  {r['durata']:5.1f}s  {r['text'][:70]}")

        mb = args.out.stat().st_size / 1024 / 1024
        print(f"\nscritto: {args.out}  ({mb:.0f} MB)")
        if scarto > 1.0:
            sys.exit(1)

if __name__ == "__main__":
    main()
