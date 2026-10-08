"""
Normalizzazione: nomi fermate canonici, regole di circolazione delle corse
(scolastica, sospesa il sabato, sostitutiva treno), deduplicazione.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .parse_pdf import PageResult, RawTrip

# ------------------------------------------------------------------ fermate

TOKEN_MAP = {"P": "PIAZZA", "PZA": "PIAZZA", "PZZA": "PIAZZA", "STAZ": "STAZIONE", "STAZ.": "STAZIONE"}


def strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))


def stop_key(name: str) -> str:
    """Chiave di confronto: maiuscolo, senza accenti/punteggiatura, abbreviazioni espanse."""
    s = strip_accents(name).upper()
    s = s.split(",")[0]                       # "PATERNO' Stazione FCE, Via V. Emanuele." -> prima parte
    s = re.sub(r"^DA\s+", "", s)
    s = re.sub(r"[()'’`.\-_/°:]", " ", s)
    toks = [TOKEN_MAP.get(t, t) for t in s.split()]
    if "METRO" in toks:                       # "CATANIA (Via Etnea) METRO BORGO" -> "METRO BORGO"
        i = toks.index("METRO")
        if i + 1 < len(toks):
            toks = toks[i:]
    return " ".join(toks)


SMALL = {"DI", "DEL", "DELLA", "DELLE", "DEI", "DA", "E"}
KEEP_UPPER = {"FCE", "S", "M", "SS", "SP", "1°", "2°"}


def pretty(name: str) -> str:
    """'S.M.LICODIA' -> 'S.M.Licodia', \"MOTTA SANT'ANASTASIA\" -> \"Motta Sant'Anastasia\"."""
    s = re.sub(r"\s+", " ", name.strip()).lower()
    s = re.sub(r"(^|[\s(.'’\-/])([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), s)
    words = s.split(" ")
    for i, w in enumerate(words):
        core = re.sub(r"[^\w]", "", w).upper()
        if core in {"FCE", "SP", "SS"}:
            words[i] = w.upper()
        elif core in SMALL and i > 0:
            words[i] = w.lower()
    s = " ".join(words)
    return s.replace("Paterno'", "Paternò").replace("Paterno’", "Paternò")


@dataclass
class StopRegistry:
    aliases: dict[str, str]                 # chiave variante -> chiave canonica
    display: dict[str, str]                 # chiave canonica -> nome visualizzato
    seen: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    codes: dict[str, str] = field(default_factory=dict)

    def resolve(self, raw_name: str, code: str | None = None) -> str:
        k = stop_key(raw_name)
        k = self.aliases.get(k, k)
        self.seen[k][raw_name] += 1
        if code:
            self.codes.setdefault(k, code)
        return k

    def name_of(self, key: str) -> str:
        if key in self.display:
            return self.display[key]
        raw = self.seen[key].most_common(1)[0][0] if self.seen.get(key) else key
        return pretty(raw)


# ------------------------------------------------------------------ regole corsa

def code_key(code: str) -> str:
    c = code.lower().replace(" ", "")
    return c


def _sentences_saturday(text: str) -> list[str]:
    """Frammenti tipo 'LE CORSE 354 - 356 - bis 880 SONO SOSPESE NELLA GIORNATA DI SABATO'."""
    t = re.sub(r"\s+", " ", text)
    out = []
    for m in re.finditer(r"sabato", t, re.I):
        window = t[max(0, m.start() - 160): m.start()]
        i = max(window.lower().rfind("corse"), window.lower().rfind("corsa"))
        if i < 0:
            continue
        frag = window[i:]
        if "sospes" in frag.lower() and len(frag) < 110:
            out.append(frag)
    return out


def saturday_codes(text: str) -> set[str]:
    """Codici corsa citati nei riquadri 'le corse X, Y sono sospese il sabato'.
    Si prendono i codici scritti dopo la parola 'corsa/corse'; se non ce ne sono
    (impaginazione rimescolata) si prendono tutti quelli del riquadro."""
    codes = set()
    for frag in text.split("\n"):
        low = frag.lower()
        compact = re.sub(r"\s+", "", low)
        if "cors" not in compact or "sospes" not in compact:
            continue
        # posizione di 'cors' nel testo originale (contando solo i caratteri non spazio)
        target, seen, cut = compact.index("cors"), 0, 0
        for i, ch in enumerate(low):
            if not ch.isspace():
                if seen == target:
                    cut = i
                    break
                seen += 1
        def grab(t):
            t = re.sub(r"(bis|ter|s)\s+(\d)", r"\1\2", t)
            return set(re.findall(r"[a-z]*\d+[a-z]*", t))
        after = grab(low[cut:])
        codes |= after if after else grab(low)
    return codes


PAGE_SCHOOL = re.compile(r"servizio[^.]{0,40}sospes(?:(?!domenic|festiv)[^.]){0,90}scuol", re.I | re.S)


@dataclass
class Trip:
    route: str
    code: str
    tipologia: str
    page: int
    stops: list[tuple[str, int, int]]      # (stop_key, arr, dep) solo fermate servite
    school: bool = False
    no_saturday: bool = False
    notes: list[str] = field(default_factory=list)
    estimated_arrival: bool = False         # collegamenti con solo orario di partenza


def letter_rules(letter: str, legend: dict[str, str]) -> tuple[bool, bool, str | None]:
    txt = legend.get(letter, "")
    if not txt:
        return False, False, None
    low = txt.lower()
    school = bool(re.search(r"scuol|scolast", low))
    nosat = "sabato" in low
    note = None if (school or nosat) else txt
    return school, nosat, note


PLACE = re.compile(r"[A-ZÀ-Ý']{4,}")


def link_trips(p: PageResult, reg: StopRegistry, durations: dict, report: dict) -> list[Trip]:
    """Tabelline 'COLLEGAMENTO A B / Partenze da A': corse con solo l'orario di partenza.
    L'arrivo viene stimato con la durata in config/rules.yaml (collegamenti)."""
    out = []
    for L in p.links:
        origin = L["origin"].strip()
        places = [x for x in PLACE.findall(L["header"]) if x not in ("COLLEGAMENTO",)]
        dests = [x for x in places if stop_key(x) != stop_key(origin)]
        if not dests:
            report.setdefault("warnings", []).append(f"p.{p.page}: collegamento da {origin} senza destinazione")
            continue
        dest = dests[0]
        a, b = reg.resolve(origin), reg.resolve(dest)
        minutes = durations.get(frozenset((stop_key(origin), stop_key(dest))))
        if minutes is None:
            minutes = durations.get("default", 15)
            report.setdefault("warnings", []).append(
                f"p.{p.page}: durata del collegamento {origin}-{dest} non configurata, uso {minutes} min stimati")
        title = f"{origin.upper()} - {dest.upper()} (NAVETTA)"
        for dep, code, school in L["departures"]:
            t = Trip(route=title, code=code or f"{dep // 60:02d}{dep % 60:02d}", tipologia="S" if school else "",
                     page=p.page, stops=[(a, dep, dep), (b, dep + minutes, dep + minutes)],
                     school=school, estimated_arrival=True)
            t.notes.append("Navetta di collegamento: arrivo stimato")
            out.append(t)
    return out


def build_trips(pages: list[PageResult], reg: StopRegistry, report: dict, durations: dict | None = None) -> list[Trip]:
    global_legend: dict[str, str] = {}
    for p in pages:
        for k, v in p.legend.items():
            global_legend.setdefault(k, v)

    trips: list[Trip] = []
    seen_sig: dict = {}
    unknown_letters = Counter()
    for p in pages:
        legend = {**global_legend, **p.legend}
        page_school = bool(PAGE_SCHOOL.search(re.sub(r"\s+", " ", p.text)))
        star_school = bool(re.search(r"scolastic", p.text, re.I)) and "*" in p.text
        sat_codes = saturday_codes(p.notes)
        for rt in p.trips:
            t = Trip(route=rt.route_title, code=rt.code, tipologia=rt.tipologia, page=rt.page, stops=[])
            for s in rt.stops:
                if s.passthrough:
                    continue
                k = reg.resolve(s.stop_name, s.stop_code)
                if t.stops and t.stops[-1][0] == k:          # stessa fermata ripetuta
                    t.stops[-1] = (k, t.stops[-1][1], s.dep)
                    continue
                t.stops.append((k, s.arr, s.dep))
            tip = rt.tipologia.upper()
            if re.fullmatch(r"TR\.?\d+", tip):
                tip = "SOST"
            if "SOST" in tip:
                t.notes.append("Bus sostitutivo del treno")
                tip = tip.replace("SOST", "")
            for letter in re.findall(r"[A-Z]", tip):
                school, nosat, note = letter_rules(letter, legend)
                if not (school or nosat or note):
                    unknown_letters[letter] += 1
                t.school |= school
                t.no_saturday |= nosat
                if note and note not in t.notes:
                    t.notes.append(note)
            if page_school:
                t.school = True
            if star_school and "*" in rt.code:
                t.school = True
            if code_key(rt.code) in sat_codes:
                t.no_saturday = True
            # stessa corsa ripetuta in due pagine (es. tabella integrativa): unisci le regole
            sig = (tuple(t.stops), code_key(t.code))
            if sig in seen_sig:
                prev = seen_sig[sig]
                prev.school |= t.school
                prev.no_saturday |= t.no_saturday
                continue
            seen_sig[sig] = t
            # due codici diversi con orari e regole identici (es. bus bis): una riga sola
            twin = (tuple(t.stops), t.school, t.no_saturday)
            if twin in seen_sig:
                continue
            seen_sig[twin] = t
            trips.append(t)
    if unknown_letters:
        report.setdefault("warnings", []).append(
            "Lettere di tipologia senza legenda: " + ", ".join(f"{k} ({v} corse)" for k, v in unknown_letters.items()))
    for p in pages:
        trips += link_trips(p, reg, durations or {}, report)
    return trips
