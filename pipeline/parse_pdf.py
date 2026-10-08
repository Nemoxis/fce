"""
Parser generico dei PDF orari FCE (autolinee).

Idea: le tabelle FCE hanno bordi disegnati come rettangoli, quindi pdfplumber
ricostruisce la griglia. Per ogni tabella:
  - le righe con almeno un orario sono "fermate";
  - le righe senza orari prima delle fermate sono "intestazioni" (codici corsa, tipologia);
  - una nuova intestazione dopo delle fermate apre un nuovo blocco (es. andata/ritorno);
  - le colonne con orari sono "corse".
Nessuna regola e' specifica di una linea: vale per tutte le pagine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pdfplumber

TIME_FULL = re.compile(r"^(\d{1,2})[.,:](\d{1,2})$")
CODE_LIKE = re.compile(r"^[A-Za-z_.*]{0,14}\d{1,4}[A-Za-z_.*]{0,10}(/\d{1,2})?$")
TIPO_TOKEN = re.compile(r"^(SOST\.?TRENI|[CSQK](/[CSQK])*)$", re.I)
TITLE_SEP = re.compile(r"\s[-/]\s")


@dataclass
class RawStopTime:
    stop_name: str
    stop_code: str | None
    arr: int | None          # minuti dalla mezzanotte
    dep: int | None
    passthrough: bool = False  # "x": transita senza fermare


@dataclass
class RawTrip:
    page: int
    route_title: str
    code: str
    tipologia: str
    stops: list[RawStopTime] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PageResult:
    page: int
    trips: list[RawTrip]
    legend: dict[str, str]
    text: str
    warnings: list[str]
    notes: str = ""        # testo delle note (fuori dalle tabelle orarie o nelle colonne a destra)
    links: list = field(default_factory=list)   # tabelline "COLLEGAMENTO" (solo orari di partenza)


# ---------------------------------------------------------------- utilita'

def squash(s: str | None) -> str:
    """Toglie spazi e a capo (gestisce '7 , 2 0', 'T 1 2 0 4')."""
    return re.sub(r"\s+", "", s or "")


def clean_text(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()


def parse_time(tok: str, warnings: list[str]) -> int | None:
    m = TIME_FULL.match(tok)
    if not m:
        return None
    h, mm = m.group(1), m.group(2)
    if len(mm) == 1:  # '7,3' -> 7:30 (numero Excel senza zero finale)
        warnings.append(f"orario '{tok}' interpretato come {h}:{mm}0")
        mm = mm + "0"
    h, mm = int(h), int(mm)
    if h > 27 or mm > 59:
        return None
    return h * 60 + mm


TIME_TOKEN = re.compile(r"(\d{1,2})[.,:](\d{2}|\d(?!\d))")


def parse_cell(raw: str | None, warnings: list[str]):
    """Ritorna ('time', arr, dep) | ('x',) | None.
    Accetta '7,20', '7 , 2 0', '6,20\n6,25' (arrivo/partenza), '19,49/19.50'."""
    s = squash(raw)
    if not s:
        return None
    if s.lower() in ("x", "x/x"):
        return ("x",)
    toks = TIME_TOKEN.findall(s)
    rest = TIME_TOKEN.sub("", s).replace("/", "")
    if rest or not 1 <= len(toks) <= 2:
        return None
    times = [parse_time(f"{h},{m}", warnings) for h, m in toks]
    if any(t is None for t in times):
        return None
    return ("time", times[0], times[-1])


def is_time_cell(raw: str | None) -> bool:
    r = parse_cell(raw, [])
    return r is not None and r[0] == "time"


def norm_code(raw: str | None) -> str:
    return squash(raw).replace("/", " ").strip()


# ---------------------------------------------------------------- griglia

def table_grid(page, table):
    """
    Matrice di testo [riga][colonna].
    Le celle unite verticalmente (bordi mancanti) vengono redistribuite:
    ogni parola va alla riga in cui cade il suo centro; se cade sul confine
    tra due righe (testo centrato su una cella unita) vale per entrambe.
    """
    rows = table.rows
    data = table.extract()
    ncols = max(len(r) for r in data)
    grid = [list(r) + [None] * (ncols - len(r)) for r in data]
    boxes = [list(r.cells) + [None] * (ncols - len(r.cells)) for r in rows]
    bands = []
    for r, row in enumerate(rows):
        bottoms = [b[3] for b in boxes[r] if b is not None]
        bands.append((row.bbox[0], row.bbox[1], row.bbox[2], min(bottoms) if bottoms else row.bbox[3]))

    # x di ogni colonna: la coppia (x0, x1) piu' frequente (le celle unite in
    # orizzontale sono rare e non devono allargare la colonna)
    from collections import Counter
    col_x = []
    for c in range(ncols):
        xs = Counter((round(boxes[r][c][0], 1), round(boxes[r][c][2], 1))
                     for r in range(len(rows)) if boxes[r][c] is not None)
        col_x.append(xs.most_common(1)[0][0] if xs else None)

    for r in range(len(rows)):
        for c in range(ncols):
            box = boxes[r][c]
            if box is None or col_x[c] is None:
                continue
            covered = [rr for rr in range(r, len(rows)) if bands[rr][1] >= box[1] - 1 and bands[rr][3] <= box[3] + 1]
            if len(covered) <= 1:
                continue
            cx0, cx1 = col_x[c]
            try:
                words = page.within_bbox((cx0 - 1, box[1], cx1 + 1, box[3])).extract_words()
            except ValueError:
                words = []
            per_row = {rr: [] for rr in covered}
            for w in words:
                if not w.get("upright", True):
                    continue
                mid = (w["top"] + w["bottom"]) / 2
                for rr in covered:
                    top, bottom = bands[rr][1], bands[rr][3]
                    if top - 1.5 <= mid <= bottom + 1.5:
                        per_row[rr].append(w["text"])
            for rr in covered:
                grid[rr][c] = "/".join(per_row[rr])
    return grid, bands, col_x


def title_candidates(page):
    out = []
    for line in page.extract_text_lines():
        t = clean_text(line["text"])
        if len(t) > 12 and len(TITLE_SEP.findall(t)) >= 1 and sum(ch.isalpha() for ch in t) > 8:
            if TIME_FULL.search(t.split()[-1] if t.split() else ""):
                continue
            if re.search(r"\d{1,2}[.,]\d{2}\s+\d{1,2}[.,]\d{2}", t):
                continue  # riga di orari, non un titolo
            out.append((line["top"], t))
    return out


def pick_title(cands, top):
    above = [c for c in cands if c[0] <= top + 5]
    pool = above or cands
    if not pool:
        return "(senza titolo)"
    if above:
        nearest = max(c[0] for c in above)
        near = [c for c in above if nearest - c[0] < 60]
        return max(near, key=lambda c: len(TITLE_SEP.findall(c[1])))[1]
    return max(pool, key=lambda c: len(c[1]))[1]


def tidy_title(t: str) -> str:
    t = re.sub(r"^(AUTOLINEA|LINEA\s+\w+|NAVETTA)\s*:?\s*", "", t, flags=re.I)
    return clean_text(t)


# ---------------------------------------------------------------- legenda

def extract_legend(grid) -> dict[str, str]:
    legend = {}
    for row in grid:
        cells = [clean_text(c) for c in row]
        for i, c in enumerate(cells):
            if re.fullmatch(r"[CSQK]", c):
                nxt = next((x for x in cells[i + 1:] if x), "")
                if len(nxt) > 8 and not is_time_cell(nxt):
                    legend[c] = nxt
    return legend


# ---------------------------------------------------------------- tabella

def parse_table(page, table, page_no, titles, warnings, notes_out: list[str]) -> tuple[list[RawTrip], dict]:
    grid, row_boxes, col_x = table_grid(page, table)
    nrows, ncols = len(grid), len(grid[0]) if grid else 0
    legend = extract_legend(grid)
    is_stop = [any(is_time_cell(grid[r][c]) for c in range(1, ncols)) for r in range(nrows)]

    # blocchi: [inizio intestazione, fine fermate]
    def looks_header(rr):
        cells = [squash(grid[rr][c]) for c in range(ncols)]
        if any(re.match(r"LOCALIT", x, re.I) for x in cells):
            return True
        return sum(1 for x in cells[1:] if CODE_LIKE.match(x) and not TIME_FULL.match(x)) >= 2

    # blocchi: (inizio intestazione, prima fermata, lista righe-fermata)
    blocks, r = [], 0
    while r < nrows:
        h0 = r
        while r < nrows and not is_stop[r]:
            r += 1
        if r >= nrows:
            break
        s0 = r
        stops = []
        while r < nrows:
            if is_stop[r]:
                stops.append(r)
                r += 1
                continue
            # righe vuote/note in mezzo alle fermate: continua se non e' una nuova intestazione
            j = r
            while j < nrows and not is_stop[j] and not looks_header(j):
                j += 1
            if j < nrows and is_stop[j]:
                r = j
                continue
            break
        blocks.append((h0, s0, stops))

    trips: list[RawTrip] = []
    max_trip_col = 0
    for (h0, s0, stop_rows) in blocks:
        header_rows = list(range(h0, s0))

        def score(rr, pat):
            return sum(1 for c in range(1, ncols) if pat(squash(grid[rr][c])))

        code_row = max(header_rows, key=lambda rr: score(rr, lambda s: bool(CODE_LIKE.match(s)) and not TIME_FULL.match(s)), default=None)
        if code_row is not None and score(code_row, lambda s: bool(CODE_LIKE.match(s))) == 0:
            code_row = None
        tipo_row = max(header_rows, key=lambda rr: score(rr, lambda s: bool(TIPO_TOKEN.match(s))), default=None)
        if tipo_row is not None and score(tipo_row, lambda s: bool(TIPO_TOKEN.match(s))) == 0:
            tipo_row = None

        n_codes = score(code_row, lambda s: bool(CODE_LIKE.match(s))) if code_row is not None else 0
        trip_cols = []
        for c in range(1, ncols):
            if not any(is_time_cell(grid[rr][c]) for rr in stop_rows):
                continue
            if n_codes >= 3 and not CODE_LIKE.match(squash(grid[code_row][c])):
                continue  # colonna senza codice corsa in una tabella che li ha: tabellina laterale
            hdr = " ".join(clean_text(grid[rr][c]) for rr in header_rows)
            if re.search(r"[A-Za-z]{9,}", squash(hdr)) and not CODE_LIKE.match(squash(grid[code_row][c]) if code_row is not None else ""):
                continue  # colonna di una tabellina laterale (es. "COLLEGAMENTO ...")
            trip_cols.append(c)
        if not trip_cols:
            continue
        max_trip_col = max(max_trip_col, trip_cols[-1])
        # colonna "codice fermata" (solo numeri) prima delle corse
        code_col = None
        for c in range(1, trip_cols[0]):
            vals = [squash(grid[rr][c]) for rr in stop_rows]
            if sum(1 for v in vals if re.fullmatch(r"\d{3,4}", v)) >= 0.6 * len(vals):
                code_col = c
                break
        end_col = code_col if code_col is not None else trip_cols[0]
        first_x = col_x[end_col][0] if col_x[end_col] else table.bbox[0] + 80
        block_top = row_boxes[h0][1]
        title = tidy_title(pick_title(titles, block_top))

        # nomi fermate: testo delle celle a sinistra delle corse; se vuoto,
        # ritaglio della pagina in quella zona (il nome a volte e' fuori dalla cella)
        names = []
        for rr in stop_rows:
            txt = clean_text(" ".join(grid[rr][c] or "" for c in range(end_col)))
            if not txt:
                _, top, _, bottom = row_boxes[rr]
                box = (table.bbox[0], top + 0.5, max(first_x - 0.5, table.bbox[0] + 1), bottom - 0.5)
                try:
                    txt = clean_text(page.crop(box).extract_text())
                except ValueError:
                    txt = ""
            code = squash(grid[rr][code_col]) if code_col is not None else None
            if code and not code.isdigit():
                code = None
            txt = re.sub(r"^(da|Da|DA)\s+", "", txt)
            txt = re.sub(r"\b(CORSA|TIPOLOGIA)\b", "", txt).strip()
            names.append((txt, code))

        for c in trip_cols:
            code = norm_code(grid[code_row][c]) if code_row is not None else ""
            if not code or not CODE_LIKE.match(squash(code)):
                code = f"col{c}"
            tipo = squash(grid[tipo_row][c]).upper().replace("SOST.TRENI", "SOST") if tipo_row is not None else ""
            trip = RawTrip(page=page_no, route_title=title, code=code, tipologia=tipo)
            for (name, scode), rr in zip(names, stop_rows):
                cell = parse_cell(grid[rr][c], trip.warnings)
                if cell is None and squash(grid[rr][c]) and re.search(r"\d[.,]\d", squash(grid[rr][c])):
                    trip.warnings.append(f"cella non interpretata a '{name}': {clean_text(grid[rr][c])!r}")
                if cell is None or not name:
                    continue
                if cell[0] == "x":
                    trip.stops.append(RawStopTime(name, scode, None, None, passthrough=True))
                else:
                    trip.stops.append(RawStopTime(name, scode, cell[1], cell[2]))
            if sum(1 for s in trip.stops if not s.passthrough) >= 2:
                trips.append(trip)
            else:
                warnings.append(f"p.{page_no} corsa {code}: meno di 2 fermate con orario, ignorata")
    if max_trip_col:
        for c in range(max_trip_col + 1, ncols):
            col_txt = " ".join((grid[r][c] or "").replace("/", " ") for r in range(nrows))
            notes_out.append(clean_text(col_txt))
    return trips, legend


def note_blocks(page, tables, keyword: str = "sabato") -> list[str]:
    """Testo dei riquadri-nota che contengono `keyword` (es. 'LE CORSE 354 - 356
    SONO SOSPESE NELLA GIORNATA DI SABATO'). Cerca la cella di tabella (o il
    riquadro) piu' piccola che contiene la parola, poi ne ricostruisce il testo
    riga per riga, senza orari."""
    words = page.extract_words()
    boxes = [c for t in tables for c in t.cells]
    boxes += [(r["x0"], r["top"], r["x1"], r["bottom"]) for r in page.rects
              if (r["x1"] - r["x0"]) > 20 and (r["bottom"] - r["top"]) > 8]
    out = []
    for w in words:
        if keyword not in w["text"].lower():
            continue
        cont = [b for b in boxes if b[0] - 1 <= w["x0"] and w["x1"] <= b[2] + 1 and b[1] - 1 <= w["top"] and w["bottom"] <= b[3] + 1]
        box = min(cont, key=lambda b: (b[2] - b[0]) * (b[3] - b[1])) if cont else \
            (w["x0"] - 150, w["top"] - 70, w["x1"] + 150, w["bottom"] + 2)
        inside = [x for x in words if box[0] - 1 <= x["x0"] and x["x1"] <= box[2] + 1
                  and box[1] - 1 <= x["top"] and x["bottom"] <= box[3] + 1
                  and not TIME_TOKEN.fullmatch(squash(x["text"])) and x["text"].lower() != "x"]
        inside.sort(key=lambda x: (round(x["top"] / 4), x["x0"]))
        toks = [x["text"] for x in inside]
        # 'B I S 1 5 9' -> 'BIS159'
        merged, buf = [], ""
        for t in toks:
            if len(t) == 1 and t.isalnum():
                buf += t
            else:
                if buf:
                    merged.append(buf); buf = ""
                merged.append(t)
        if buf:
            merged.append(buf)
        txt = " ".join(merged)
        if len(txt) < 300:
            out.append(txt)
    return out


LINK_TIME = re.compile(r"^(\d{1,2})[.,:](\d{2})$")


def parse_links(page):
    """Tabelline di collegamento: un riquadro con 'Partenze da <localita'>' seguito da un
    elenco di orari di partenza (ed eventualmente 'Scolastica' e un codice corsa).
    Sopra c'e' un titolo tipo 'COLLEGAMENTO CASTIGLIONE LINGUAGLOSSA'.
    Ritorna [{header, origin, departures:[(minuti, codice, scolastica)]}]."""
    words = [w for w in page.extract_words(extra_attrs=["upright"]) if w.get("upright", True)]
    out = []
    anchors = [w for w in words if w["text"].lower() == "partenze"]
    for a in anchors:
        da = next((w for w in words if w["text"].lower() == "da" and abs(w["top"] - a["top"]) < 3
                   and 0 < w["x0"] - a["x1"] < 15), None)
        if not da:
            continue
        x0 = a["x0"] - 18
        below = sorted([w for w in words if w["x0"] >= x0 and w["top"] > a["top"] + 2], key=lambda w: (round(w["top"]), w["x0"]))
        lines, cur, top = [], [], None
        for w in below:
            if top is None or abs(w["top"] - top) > 3:
                if cur:
                    lines.append(cur)
                cur, top = [w], w["top"]
            else:
                cur.append(w)
        if cur:
            lines.append(cur)
        origin, deps, last_y = [], [], None
        for l in lines:
            l = sorted(l, key=lambda w: w["x0"])
            first = l[0]["text"]
            m = LINK_TIME.match(first)
            if not m:
                if deps:
                    break
                if len(origin) < 2 and not any(LINK_TIME.match(w["text"]) for w in l):
                    origin.append(" ".join(w["text"] for w in l))
                    continue
                break
            if last_y is not None and l[0]["top"] - last_y > 30:
                break
            last_y = l[0]["top"]
            rest = " ".join(w["text"] for w in l[1:])
            code = re.findall(r"\b(s?\d{3,4})\b", rest)
            deps.append((int(m.group(1)) * 60 + int(m.group(2)), code[-1] if code else "", "scolast" in rest.lower()))
        if not deps or not origin:
            continue
        # titolo del riquadro: testo sopra 'Partenze', ricostruito con tolleranza larga (lettere spaziate)
        box = (x0, max(0, a["top"] - 60), min(page.width, a["x1"] + 70), a["top"] - 1)
        chars = [c for c in page.chars if c.get("upright", True) and box[0] <= c["x0"] and c["x1"] <= box[2]
                 and box[1] <= c["top"] and c["bottom"] <= box[3]]
        rows = {}
        for c in chars:
            rows.setdefault(round(c["top"] / 3), []).append(c)
        head = " ".join("".join(c["text"] for c in sorted(r, key=lambda c: c["x0"])) for _, r in sorted(rows.items()))
        head = re.sub(r"\s+", " ", head)
        if "collegamento" not in head.lower():
            continue  # non e' una tabellina di collegamento
        out.append({"header": head.strip(), "origin": " ".join(origin), "departures": deps})
    return out


def parse_pdf(path: str) -> list[PageResult]:
    results = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            warnings: list[str] = []
            tables = page.find_tables()
            timed = [tb for tb in tables if sum(is_time_cell(x) for row in tb.extract() for x in row) >= 3]
            titles = [c for c in title_candidates(page)
                      if not any(tb.bbox[1] + 40 < c[0] < tb.bbox[3] for tb in timed)]
            trips, legend = [], {}
            notes: list[str] = []
            for t in tables:
                tt, lg = parse_table(page, t, i, titles, warnings, notes)
                trips += tt
                legend.update(lg)
            text = page.extract_text() or ""
            notes = note_blocks(page, tables, "sabato")
            if not trips and len(re.findall(r"\d{1,2}[.,]\d{2}", text)) > 10:
                warnings.append(f"p.{i}: ci sono orari nel testo ma nessuna tabella riconosciuta (pagina da verificare)")
            results.append(PageResult(i, trips, legend, text, warnings, "\n".join(notes), parse_links(page)))
    return results


if __name__ == "__main__":
    import sys
    for pr in parse_pdf(sys.argv[1]):
        print(f"--- pagina {pr.page}: {len(pr.trips)} corse, legenda {pr.legend}")
        for w in pr.warnings:
            print("   !", w)
        for t in pr.trips:
            seq = " > ".join(f"{s.stop_name}{'(x)' if s.passthrough else ' %02d:%02d' % divmod(s.dep, 60)}" for s in t.stops)
            print(f"  [{t.code}|{t.tipologia}] {t.route_title[:40]} :: {seq[:200]}")
