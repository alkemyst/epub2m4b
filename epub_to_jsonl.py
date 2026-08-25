#!/usr/bin/env python3
"""EPUB -> JSONL di segmenti pronti per il TTS."""
import json, re, argparse, warnings
from pathlib import Path
from ebooklib import epub, ITEM_DOCUMENT
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore")

ABBR = r"(?<!\bsig)(?<!\bdott)(?<!\bprof)(?<!\bavv)(?<!\bing)(?<!\bon)(?<!\becc)(?<!\bcap)(?<!\bpag)(?<!\bvol)(?<!\bart)(?<!\brev)(?<!\bn)"
FINE_FRASE = re.compile(ABBR + r"([.!?\u2026]+[\"\u201d\u00bb')\]]*)\s+(?=[\"\u201c\u00abA-Z\u00c0-\u00dc0-9])")

def paragrafi(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "sup", "sub"]):   # sup/sub = note a pie' di pagina
        t.decompose()
    out = []
    for el in soup.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote"]):
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        if len(txt) >= 2:
            out.append(txt)
    if not out:
        txt = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
        if txt:
            out = [txt]
    return out

def frasi(par: str) -> list[str]:
    parti = FINE_FRASE.sub(r"\1\n", par).split("\n")
    return [p.strip() for p in parti if p.strip()]

def raggruppa(fr: list[str], maxlen: int) -> list[str]:
    blocchi, cur = [], ""
    for f in fr:
        if not cur:
            cur = f
        elif len(cur) + 1 + len(f) <= maxlen:
            cur += " " + f
        else:
            blocchi.append(cur)
            cur = f
    if cur:
        blocchi.append(cur)
    return blocchi

UNITA = ["zero", "uno", "due", "tre", "quattro", "cinque", "sei", "sette", "otto",
         "nove", "dieci", "undici", "dodici", "tredici", "quattordici", "quindici",
         "sedici", "diciassette", "diciotto", "diciannove"]
DECINE = {2: "venti", 3: "trenta", 4: "quaranta", 5: "cinquanta",
          6: "sessanta", 7: "settanta", 8: "ottanta", 9: "novanta"}

def parola_numero(n: int) -> str:
    """1 -> 'uno', 23 -> 'ventitre'' . Oltre il 99 lascia le cifre al TTS."""
    if n < 20:
        return UNITA[n]
    if n < 100:
        d, u = divmod(n, 10)
        base = DECINE[d]
        if u == 0:
            return base
        if u in (1, 8):
            base = base[:-1]            # venti + uno -> ventuno, venti + otto -> ventotto
        parola = base + UNITA[u]
        if u == 3:
            parola = parola[:-1] + "é"   # ventitre -> ventitre' con accento
        return parola
    return str(n)

# '1.', '2)', 'Capitolo 3', '5. Il ritorno'
INTESTAZIONE = re.compile(r"^\s*(?:capitolo\s+)?(\d{1,3})\s*[.)\]]?\s*(.*)$", re.I)

def intestazione(par: str):
    """Riconosce un'intestazione di capitolo numerata.

    Torna (titolo_visibile, testo_da_leggere) oppure None. Applicata solo al
    primo paragrafo di un capitolo, e solo se e' corta: cosi' un paragrafo che
    comincia per cifra (es. '1943 fu l'anno...') non viene scambiato per titolo.
    """
    m = INTESTAZIONE.match(par)
    if not m:
        return None
    numero, resto = int(m.group(1)), m.group(2).strip()
    if resto and len(par) > 80:
        return None
    titolo = f"Capitolo {numero}" + (f". {resto}" if resto else "")
    testo = f"Capitolo {parola_numero(numero)}." + (f" {resto}" if resto else "")
    return titolo, testo

def parse_intervalli(s: str) -> set[int]:
    """'1-4,29,30' -> {1,2,3,4,29,30}"""
    out = set()
    for pezzo in s.split(","):
        pezzo = pezzo.strip()
        if not pezzo:
            continue
        if "-" in pezzo:
            a, b = pezzo.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(pezzo))
    return out

def spezza_lungo(s: str, maxlen: int) -> list[str]:
    """Ultima risorsa: una frase piu' lunga del limite, tagliata sulle virgole."""
    if len(s) <= maxlen:
        return [s]
    pezzi, cur = [], ""
    for tratto in re.split(r"(?<=[,;:])\s+", s):
        if not cur:
            cur = tratto
        elif len(cur) + 1 + len(tratto) <= maxlen:
            cur += " " + tratto
        else:
            pezzi.append(cur)
            cur = tratto
    if cur:
        pezzi.append(cur)
    return pezzi

def segmenta(ncap: int, pars: list[str], maxlen: int) -> list[dict]:
    """Paragrafi di un capitolo -> righe del JSONL."""
    righe, nseg = [], 0
    intest = intestazione(pars[0]) if pars else None
    titolo = intest[0] if intest else (pars[0][:80] if pars else None)

    for ip, par in enumerate(pars):
        if ip == 0 and intest:
            blocchi = [intest[1]]        # '1.' letto come 'Capitolo uno.'
        else:
            blocchi = []
            for b in raggruppa(frasi(par), maxlen):
                blocchi.extend(spezza_lungo(b, maxlen))
        for ib, b in enumerate(blocchi):
            nseg += 1
            righe.append({
                "id": f"ch{ncap:02d}_{nseg:04d}",
                "chapter": ncap,
                "titolo": titolo if ip == 0 and ib == 0 else None,
                "text": b,
                "fine_paragrafo": ib == len(blocchi) - 1,
            })
    return righe

def main():
    p = argparse.ArgumentParser()
    p.add_argument("epub", type=Path)
    p.add_argument("-o", "--out", type=Path, default=None)
    p.add_argument("--maxlen", type=int, default=280)
    p.add_argument("--min-caratteri", type=int, default=200,
                   help="scarta i documenti piu' corti (copertine, colophon)")
    p.add_argument("--salta-capitoli", default="",
                   help="numeri da escludere, es. '1-4,29,30'. La numerazione "
                        "degli altri capitoli non cambia")
    p.add_argument("--lista-capitoli", action="store_true",
                   help="stampa la tabella dei capitoli ed esce, senza scrivere")
    args = p.parse_args()

    out = args.out or args.epub.with_suffix(".jsonl")
    libro = epub.read_epub(str(args.epub))
    salta = parse_intervalli(args.salta_capitoli)

    # prima passata: numera i documenti che superano il filtro di lunghezza.
    # Il numero e' assegnato qui e non cambia mai, cosi' gli id restano stabili
    # anche se in seguito si modifica la lista dei capitoli da saltare.
    capitoli, ncap = [], 0
    for item in libro.get_items_of_type(ITEM_DOCUMENT):
        pars = paragrafi(item.get_content().decode("utf-8", "ignore"))
        if sum(len(x) for x in pars) < args.min_caratteri:
            continue
        ncap += 1
        capitoli.append((ncap, segmenta(ncap, pars, args.maxlen)))

    if args.lista_capitoli:
        print(f"{'cap':>4} {'segm':>5} {'caratteri':>10}  titolo")
        for n, righe in capitoli:
            car = sum(len(r["text"]) for r in righe)
            tit = righe[0]["titolo"] or "" if righe else ""
            print(f"{n:4d} {len(righe):5d} {car:10d}  {tit[:60]}")
        print(f"\n{len(capitoli)} capitoli. Usa --salta-capitoli per escluderne.")
        return

    tenuti = [(n, righe) for n, righe in capitoli if n not in salta]
    righe = [r for _, rr in tenuti for r in rr]

    with out.open("w") as f:
        for r in righe:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    esclusi = sorted(n for n, _ in capitoli if n in salta)
    if esclusi:
        persi = sum(len(rr) for n, rr in capitoli if n in salta)
        print(f"saltati {len(esclusi)} capitoli ({persi} segmenti): "
              + ", ".join(map(str, esclusi)))
    ignoti = sorted(salta - {n for n, _ in capitoli})
    if ignoti:
        print(f"attenzione: capitoli inesistenti in --salta-capitoli: "
              + ", ".join(map(str, ignoti)))

    car = sum(len(r["text"]) for r in righe)
    print(f"{len(tenuti)} capitoli, {len(righe)} segmenti, {car} caratteri")
    print(f"durata stimata: {car / 14 / 3600:.1f} h")

    corti = [r for r in righe if len(r["text"]) < 10]
    if corti:
        print(f"attenzione: {len(corti)} segmenti sotto i 10 caratteri, "
              "instabili per il TTS:")
        for r in corti[:10]:
            print(f"  {r['id']}  {r['text']!r}")

    print(f"scritto: {out}")

if __name__ == "__main__":
    main()

