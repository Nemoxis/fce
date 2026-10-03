"""
Controlli automatici. Eseguili con:  python -m tests.test_orari

1. Su tutti i dati pubblicati: orari crescenti, riferimenti validi, index coerente.
2. "Golden test": alcune corse verificate a mano sul PDF del 15 settembre 2026.
   Se modifichi il parser e questi falliscono, hai rotto qualcosa.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
GOLDEN_PDF = ROOT / "pdf_archive" / "FCE_AUTOBUS_DAL_15_SETTEMBRE_2026.pdf"

fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


def hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def test_published() -> None:
    idx = json.loads((DATA / "index.json").read_text(encoding="utf-8"))
    check(bool(idx.get("versions")), "index.json senza versioni")
    for v in idx.get("versions", []):
        d = json.loads((DATA / v["file"]).read_text(encoding="utf-8"))
        check(len(d["trips"]) == v["trips"], f"{v['file']}: numero corse diverso dall'indice")
        ns, nr = len(d["stops"]), len(d["routes"])
        for t in d["trips"]:
            check(0 <= t["r"] < nr, f"{v['file']} corsa {t['c']}: linea inesistente")
            prev = -1
            for sid, arr, dep in t["s"]:
                check(0 <= sid < ns, f"{v['file']} corsa {t['c']}: fermata inesistente")
                check(arr >= prev and dep >= arr, f"{v['file']} corsa {t['c']}: orari non crescenti")
                prev = dep


def test_golden() -> None:
    if not GOLDEN_PDF.exists():
        print("(golden test saltato: PDF di riferimento non presente)")
        return
    from pipeline.build import load_aliases
    from pipeline.normalize import StopRegistry, build_trips
    from pipeline.parse_pdf import parse_pdf

    pages = parse_pdf(str(GOLDEN_PDF))
    reg = StopRegistry(*load_aliases())
    trips = build_trips(pages, reg, {})
    a18 = {t.code: t for t in trips if "A18" in t.route}

    def seq(code):
        return [(k, hm(dep)) for k, _, dep in a18[code].stops]

    check(len(trips) == 248, f"attese 248 corse, trovate {len(trips)}")
    check(len(a18) == 38, f"linea A18: attese 38 corse, trovate {len(a18)}")
    check(seq("205") == [
        ("RANDAZZO STAZIONE FCE", "07:00"), ("SOLICCHIATA", "07:20"), ("LINGUAGLOSSA STAZIONE FCE", "07:30"),
        ("PIEDIMONTE", "07:40"), ("FIUMEFREDDO CONTRADA PONTE BORIA", "07:50"), ("METRO BORGO", "08:20"),
        ("CATANIA PIAZZA S M DI GESU", "08:27"), ("METRO MILO", "08:35"), ("CATANIA S SOFIA", "08:45"),
    ], f"corsa 205 diversa dal PDF: {seq('205')}")
    check(seq("603")[-3:] == [("GIARRE PARCO JUNGO", "07:40"), ("GIARRE", "07:40"), ("RIPOSTO STAZIONE FCE", "07:50")],
          f"corsa 603 (celle unite) diversa dal PDF: {seq('603')}")
    check(seq("650") == [("LINGUAGLOSSA STAZIONE FCE", "06:50"), ("RANDAZZO STAZIONE FCE", "07:20")], "corsa 650 errata")
    check(a18["203"].stops[2][1:] == (6 * 60 + 19, 6 * 60 + 20), "corsa 203: doppio orario 06.19/06.20 a Linguaglossa non letto")
    check(a18["201"].school and a18["201"].no_saturday, "corsa 201 (C/S): dovrebbe essere scolastica e sospesa il sabato")
    check(not a18["205"].school and not a18["205"].no_saturday, "corsa 205: non ha limitazioni")
    check(any("sostitutivo" in n.lower() for n in a18["TR.1"].notes), "corsa TR.1: dovrebbe essere sostitutiva treno")
    belpasso = {t.code: t for t in trips if t.route.startswith("BELPASSO")}
    check(belpasso["853"].no_saturday, "corsa 853: la nota dice sospesa il sabato")
    check(not belpasso["851"].no_saturday, "corsa 851: circola anche il sabato")
    adr = {t.code: t for t in trips if t.route.startswith("ADRANO")}
    check(adr["BIS159"].no_saturday and adr["165"].no_saturday, "corse BIS159/165: sospese il sabato")
    lin = [t for t in trips if t.code == "301"]
    check(len(lin) == 1 and lin[0].school, "corsa 301 (Lineri): una sola, scolastica")
    check(belpasso["354"].no_saturday and belpasso["356"].no_saturday, "corse 354/356: sospese il sabato")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    test_published()
    test_golden()
    if fails:
        print("TEST FALLITI:")
        for f in fails:
            print(" -", f)
        sys.exit(1)
    print("Tutti i controlli superati.")
