"""
Controlli prima di pubblicare.
- Per corsa: almeno 2 fermate, orari non decrescenti, durata plausibile.
  Le corse che non passano vengono scartate e finiscono nel report.
- Globali (bloccanti): nessuna corsa valida, troppe corse scartate,
  calo drastico rispetto alla versione precedente.
"""
from __future__ import annotations

from .normalize import Trip

MAX_TRIP_MINUTES = 300
MAX_INVALID_RATIO = 0.15
MIN_RATIO_VS_PREVIOUS = 0.5


def check_trip(t: Trip) -> str | None:
    if len(t.stops) < 2:
        return "meno di 2 fermate"
    prev = None
    for key, arr, dep in t.stops:
        if arr is None or dep is None:
            return f"orario mancante a {key}"
        if dep < arr:
            return f"partenza prima dell'arrivo a {key}"
        if prev is not None and arr < prev:
            return f"orario che torna indietro a {key} ({arr // 60}:{arr % 60:02d} dopo {prev // 60}:{prev % 60:02d})"
        prev = dep
    dur = t.stops[-1][2] - t.stops[0][2]
    if dur > MAX_TRIP_MINUTES:
        return f"durata non plausibile ({dur} min)"
    return None


def validate(trips: list[Trip], previous_count: int | None, report: dict) -> tuple[list[Trip], bool]:
    good, bad = [], []
    for t in trips:
        err = check_trip(t)
        if err:
            bad.append({"pagina": t.page, "corsa": t.code, "linea": t.route, "errore": err})
        else:
            good.append(t)
    report["corse_valide"] = len(good)
    report["corse_scartate"] = bad
    blocking = []
    if not good:
        blocking.append("nessuna corsa valida estratta")
    total = len(good) + len(bad)
    if total and len(bad) / total > MAX_INVALID_RATIO:
        blocking.append(f"troppe corse scartate ({len(bad)}/{total})")
    if previous_count and len(good) < previous_count * MIN_RATIO_VS_PREVIOUS:
        blocking.append(f"corse crollate rispetto alla versione precedente ({len(good)} contro {previous_count})")
    report["errori_bloccanti"] = blocking
    return good, not blocking
