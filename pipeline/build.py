"""
Da un PDF orari a JSON per l'app.

Uso:
  python -m pipeline.build --pdf percorso.pdf [--valid-from 2026-09-15] [--source-url URL]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import yaml

from .normalize import StopRegistry, build_trips, stop_key
from .parse_pdf import parse_pdf
from .validate import validate

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "docs" / "data"

MESI = {m: i for i, m in enumerate(
    ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
     "agosto", "settembre", "ottobre", "novembre", "dicembre"], start=1)}


def italian_date(text: str) -> str | None:
    m = re.search(r"(\d{1,2})\s*[- ]\s*(" + "|".join(MESI) + r")\s*[- ]\s*(\d{4})", text, re.I)
    if not m:
        return None
    return dt.date(int(m.group(3)), MESI[m.group(2).lower()], int(m.group(1))).isoformat()


def valid_from_pdf(pages) -> str | None:
    found = []
    for p in pages:
        for m in re.finditer(r"in\s+vigore\s+dal:?\s*([^\n]{0,30})", p.text, re.I):
            d = italian_date(m.group(1))
            if d:
                found.append(d)
    if not found:
        return None
    return max(set(found), key=found.count)


def easter(y: int) -> dt.date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(y, month, day)


def build_calendar(years: list[int]) -> dict:
    cfg = yaml.safe_load((CONFIG / "calendar.yaml").read_text(encoding="utf-8"))
    hol = set()
    for y in years:
        for md in cfg["festivita_nazionali"]:
            hol.add(f"{y}-{md}")
        e = easter(y)
        hol.add(e.isoformat())
        hol.add((e + dt.timedelta(days=1)).isoformat())
    hol |= set(cfg.get("festivita_extra") or [])
    warns = [{"data": f"{y}-{w['data']}", "nome": w["nome"]} for y in years for w in (cfg.get("avvisi_feste_patronali") or [])]
    return {
        "festivi": sorted(hol),
        "avvisi": warns,
        "scuola": cfg.get("anni_scolastici") or [],
    }


def load_aliases() -> tuple[dict, dict]:
    path = CONFIG / "aliases.yaml"
    if not path.exists():
        return {}, {}
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    aliases, display = {}, {}
    for canon, spec in (cfg.get("fermate") or {}).items():
        ck = stop_key(canon)
        display[ck] = spec.get("nome", canon) if isinstance(spec, dict) else canon
        for v in (spec.get("varianti", []) if isinstance(spec, dict) else []):
            aliases[stop_key(v)] = ck
    return aliases, display


def route_rules(route_title: str, stop_keys: set[str], report: dict) -> list[list[str]]:
    cfg = yaml.safe_load((CONFIG / "rules.yaml").read_text(encoding="utf-8")) or {}
    groups = []
    for rule in cfg.get("linee", []):
        if rule["titolo_contiene"].upper() in route_title.upper():
            for g in rule.get("no_tratte_locali", []):
                members = sorted(k for k in stop_keys if any(k.startswith(p) for p in g))
                if len(members) >= 2:
                    groups.append(members)
    return groups


def route_label(title: str) -> tuple[str, str]:
    """(nome breve, nome completo). Es. 'Randazzo – Catania (via A18)'."""
    from .normalize import pretty
    t = re.sub(r"\s*-\s*-\s*", " - ", title)
    parts = [pretty(p.strip()) for p in re.split(r"\s+[-/]\s+", t) if p.strip()]
    full = " – ".join(parts)
    if len(parts) < 2:
        return full, full
    via = re.search(r"\((via [^)]*)\)", parts[-1], re.I)
    first, last = parts[0], re.sub(r"\s*\(via [^)]*\)", "", parts[-1], flags=re.I)
    middle = parts[1:-1]
    if first == last and middle:
        return f"Circolare {first} – {middle[len(middle) // 2]}", full
    if via:
        return f"{first} – {last} (via {via.group(1)[4:]})", full
    if not middle:
        return f"{first} – {last}", full
    picks = middle if len(middle) <= 3 else [middle[0], middle[len(middle) // 2], middle[-1]]
    return f"{first} – {last}, via {', '.join(picks)}", full


def build(pdf_path: Path, valid_from: str | None = None, source_url: str | None = None,
          previous_count: int | None = None, out_dir: Path = DATA) -> dict:
    report: dict = {"pdf": pdf_path.name, "warnings": []}
    pages = parse_pdf(str(pdf_path))
    for p in pages:
        report["warnings"] += p.warnings
        for t in p.trips:
            report["warnings"] += [f"p.{t.page} corsa {t.code}: {w}" for w in t.warnings]

    vf = valid_from or valid_from_pdf(pages)
    if not vf:
        raise SystemExit("Impossibile capire la data 'in vigore dal': passala con --valid-from AAAA-MM-GG")
    report["valid_from"] = vf

    aliases, display = load_aliases()
    reg = StopRegistry(aliases=aliases, display=display)
    trips = build_trips(pages, reg, report)
    trips, ok = validate(trips, previous_count, report)

    note_cfg = yaml.safe_load((CONFIG / "rules.yaml").read_text(encoding="utf-8")).get("controlla_nota")
    stop_index: dict[str, int] = {}
    stops_out, routes_out, route_index, trips_out = [], [], {}, []
    route_stops: dict[str, set] = {}
    for t in trips:
        route_stops.setdefault(t.route, set()).update(k for k, _, _ in t.stops)

    for t in sorted(trips, key=lambda t: (t.route, t.stops[0][2])):
        if t.route not in route_index:
            route_index[t.route] = len(routes_out)
            groups = route_rules(t.route, route_stops[t.route], report)
            short, full = route_label(t.route)
            routes_out.append({"name": short, "full": full, "raw": t.route, "noLocal": groups})
        seq = []
        for k, arr, dep in t.stops:
            if k not in stop_index:
                stop_index[k] = len(stops_out)
                stops_out.append({"k": k, "name": reg.name_of(k), "code": reg.codes.get(k)})
            seq.append([stop_index[k], arr, dep])
        flags = (["school"] if t.school else []) + (["nosat"] if t.no_saturday else [])
        trips_out.append({"r": route_index[t.route], "c": t.code, "t": t.tipologia, "p": t.page,
                          "f": flags, "n": t.notes, "s": seq})

    for r in routes_out:
        r["noLocal"] = [[stop_index[k] for k in g if k in stop_index] for g in r["noLocal"]]
    if note_cfg:
        for p in pages:
            if note_cfg.lower() in re.sub(r"\s+", " ", p.text).lower():
                titles = {t.route_title for t in p.trips}
                for title in titles:
                    if title in route_index and not routes_out[route_index[title]]["noLocal"]:
                        report["warnings"].append(f"p.{p.page}: nota '{note_cfg}' presente ma nessuna regola in config/rules.yaml per '{title}'")

    sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    years = sorted({int(vf[:4]), int(vf[:4]) + 1})
    timetable = {
        "valid_from": vf,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source": {"file": pdf_path.name, "url": source_url, "sha256": sha},
        "calendar": build_calendar(years),
        "stops": stops_out,
        "routes": routes_out,
        "trips": trips_out,
    }
    report["fermate"] = sorted({s["name"] for s in stops_out})
    report["ok"] = ok
    if ok:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"orario-{vf}.json").write_text(json.dumps(timetable, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (ROOT / "state" / "reports").mkdir(parents=True, exist_ok=True)
    (ROOT / "state" / "reports" / f"report-{vf}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def update_index(out_dir: Path = DATA, status: dict | None = None) -> dict:
    idx_path = out_dir / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {}
    versions = []
    for f in sorted(out_dir.glob("orario-*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        versions.append({"valid_from": d["valid_from"], "file": f.name, "trips": len(d["trips"]),
                         "source": d["source"], "generated": d["generated"]})
    idx["versions"] = versions
    if status is not None:
        idx["status"] = status
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    return idx


def main():
    ap = argparse.ArgumentParser(description="Converte un PDF orari FCE in dati per l'app")
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--valid-from")
    ap.add_argument("--source-url")
    a = ap.parse_args()
    rep = build(a.pdf, a.valid_from, a.source_url)
    update_index()
    print(json.dumps({k: rep[k] for k in ("valid_from", "ok", "corse_valide", "errori_bloccanti")}, ensure_ascii=False, indent=1))
    print(f"Corse scartate: {len(rep['corse_scartate'])}  |  avvisi: {len(rep['warnings'])}")
    print("Report completo in state/reports/")


if __name__ == "__main__":
    main()
