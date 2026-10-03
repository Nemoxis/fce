"""
Controllo aggiornamenti: legge la pagina "Le nostre linee", trova i PDF degli orari
autolinee, scarica quelli nuovi, li converte e pubblica i dati se la validazione passa.
Elabora anche i PDF messi a mano nella cartella pdf_inbox/.

Uso:  python -m pipeline.update            (controllo normale)
      python -m pipeline.update --force    (rielabora anche i PDF gia' visti)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

from .build import DATA, ROOT, build, italian_date, update_index

STATE = ROOT / "state" / "state.json"
ARCHIVE = ROOT / "pdf_archive"
INBOX = ROOT / "pdf_inbox"
SOURCES = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
# per i test: si puo' puntare a una copia locale del sito
SOURCES["pagina_orari"] = os.environ.get("FCE_PAGINA_ORARI", SOURCES["pagina_orari"])
SOURCES["sito"] = os.environ.get("FCE_SITO", SOURCES["sito"])
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.6",
}


def get(session: requests.Session, url: str, tries: int = 3) -> requests.Response:
    """GET con tentativi ripetuti (il sito a volte e' lento)."""
    import time
    last = None
    for i in range(tries):
        try:
            r = session.get(url, headers=UA, timeout=60, allow_redirects=True)
            if r.status_code < 500:
                return r
            last = requests.HTTPError(f"{r.status_code} per {url}")
        except requests.RequestException as e:
            last = e
        time.sleep(5 * (i + 1))
    raise last


# ------------------------------------------------------------------ notifiche

def notify(text: str) -> None:
    print("[notifica]", text)
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=20)
    except requests.RequestException as e:
        print("Telegram non raggiungibile:", e)


# ------------------------------------------------------------------ stato

def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"packages": {}}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ sito FCE

def find_packages(html: str, base: str) -> list[dict]:
    """Trova i pacchetti WP Download Manager con il loro titolo."""
    soup = BeautifulSoup(html, "html.parser")
    pkgs: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        q = parse_qs(urlparse(href).query)
        slug_m = re.search(r"/download/([^/?#]+)/?", href)
        pid = q.get("wpdmdl", [None])[0]
        if not pid and not slug_m:
            continue
        key = pid or slug_m.group(1)
        title = a.get("title") or a.get_text(" ", strip=True)
        if len(title) < 8 or title.lower() in ("download", "scarica"):
            heading = a.find_previous(["h1", "h2", "h3", "h4", "h5", "strong"])
            title = heading.get_text(" ", strip=True) if heading else ""
        if not title and slug_m:
            title = slug_m.group(1).replace("-", " ")
        p = pkgs.setdefault(key, {"id": key, "title": title, "links": []})
        if len(title) > len(p["title"]):
            p["title"] = title
        if href not in p["links"]:
            p["links"].append(href)
    return list(pkgs.values())


def categorize(title: str) -> str | None:
    t = title.lower()
    for cat, spec in SOURCES["categorie"].items():
        if any(k in t for k in spec["parole"]) and not any(k in t for k in spec.get("escludi", [])):
            return cat
    return None


def download_pdf(pkg: dict, session: requests.Session) -> bytes | None:
    candidates = []
    if pkg["id"].isdigit():
        candidates.append(f"{SOURCES['sito']}/?wpdmdl={pkg['id']}")
    candidates += pkg["links"]
    for url in candidates:
        try:
            r = get(session, url)
        except requests.RequestException:
            continue
        if r.content[:5] == b"%PDF-":
            return r.content
        if "html" in r.headers.get("content-type", ""):
            # pagina del pacchetto: cerca link con wpdmdl
            for p in find_packages(r.text, url):
                for link in p["links"]:
                    if "wpdmdl" in link:
                        try:
                            r2 = get(session, link)
                            if r2.content[:5] == b"%PDF-":
                                return r2.content
                        except requests.RequestException:
                            pass
    return None


# ------------------------------------------------------------------ elaborazione

def previous_trip_count() -> int | None:
    idx = DATA / "index.json"
    if not idx.exists():
        return None
    v = json.loads(idx.read_text(encoding="utf-8")).get("versions", [])
    return max((x["trips"] for x in v), default=None)


def process_pdf(path: Path, valid_from: str | None, source_url: str | None, label: str) -> dict:
    try:
        rep = build(path, valid_from, source_url, previous_count=previous_trip_count())
    except BaseException as e:            # anche SystemExit (es. data non trovata): mai bloccare il controllo
        rep = {"ok": False, "valid_from": valid_from or italian_date(label),
               "errori_bloccanti": [f"il PDF non e' stato letto ({type(e).__name__}: {e})"],
               "corse_valide": 0, "corse_scartate": [], "warnings": []}
    if rep["ok"]:
        notify(f"Orari FCE: pubblicato \"{label}\" (in vigore dal {rep['valid_from']}), "
               f"{rep['corse_valide']} corse, {len(rep['corse_scartate'])} scartate, {len(rep['warnings'])} avvisi.")
    else:
        notify(f"Orari FCE: nuovo PDF \"{label}\" NON pubblicato. Motivo: {'; '.join(rep['errori_bloccanti'])}. "
               f"L'app continua a mostrare la versione precedente. Vedi state/reports/.")
    return rep


def prune_versions(keep_past: int = 1) -> None:
    """Tiene le versioni future, quella in vigore e `keep_past` precedenti."""
    today = dt.date.today().isoformat()
    files = sorted(DATA.glob("orario-*.json"))
    past = [f for f in files if f.stem.replace("orario-", "") <= today]
    for f in past[: max(0, len(past) - 1 - keep_past)]:
        f.unlink()


def run(force: bool = False) -> int:
    st = load_state()
    session = requests.Session()
    errors = []
    try:
        r = get(session, SOURCES["pagina_orari"])
        r.raise_for_status()
        pkgs = find_packages(r.text, SOURCES["pagina_orari"])
    except requests.RequestException as e:
        pkgs = []
        errors.append(f"sito FCE non raggiungibile: {e}")

    found = 0
    for pkg in pkgs:
        cat = categorize(pkg["title"])
        if cat not in SOURCES["elabora"]:
            continue
        found += 1
        known = st["packages"].get(pkg["id"])
        today = dt.date.today().isoformat()
        # gia' elaborato con successo: lo riscarico al massimo una volta al giorno
        # per accorgermi se FCE ha sostituito il file mantenendo lo stesso titolo
        if known and known.get("status") == "ok" and known.get("title") == pkg["title"] \
                and known.get("verified") == today and not force:
            continue
        pdf = download_pdf(pkg, session)
        if not pdf:
            errors.append(f"download fallito per '{pkg['title']}'")
            st["packages"][pkg["id"]] = {"title": pkg["title"], "status": "download_failed",
                                         "url": pkg["links"][0], "reason": "impossibile scaricare il PDF",
                                         "valid_from": italian_date(pkg["title"]),
                                         "checked": dt.datetime.now().isoformat(timespec="minutes")}
            continue
        sha = hashlib.sha256(pdf).hexdigest()
        if known and known.get("sha256") == sha and not force:
            known["verified"] = today
            continue
        vf = italian_date(pkg["title"])
        ARCHIVE.mkdir(exist_ok=True)
        path = ARCHIVE / f"{cat}-{vf or pkg['id']}.pdf"
        path.write_bytes(pdf)
        rep = process_pdf(path, None, pkg["links"][0], pkg["title"])
        st["packages"][pkg["id"]] = {"title": pkg["title"], "category": cat, "sha256": sha,
                                     "valid_from": rep.get("valid_from") or vf, "status": "ok" if rep["ok"] else "failed",
                                     "url": pkg["links"][0], "reason": "; ".join(rep.get("errori_bloccanti") or []),
                                     "processed": dt.datetime.now().isoformat(timespec="minutes"), "verified": today}

    if pkgs and not found:
        errors.append("nessun PDF 'autolinee' trovato nella pagina: forse il sito e' cambiato")

    # PDF caricati a mano
    INBOX.mkdir(exist_ok=True)
    for f in sorted(INBOX.glob("*.pdf")):
        ARCHIVE.mkdir(exist_ok=True)
        dest = ARCHIVE / f.name
        shutil.move(str(f), dest)
        rep = process_pdf(dest, None, None, f.name)
        st["packages"][f"manuale:{f.name}"] = {"title": f.name, "status": "ok" if rep["ok"] else "failed",
                                               "valid_from": rep.get("valid_from"), "url": None,
                                               "reason": "; ".join(rep.get("errori_bloccanti") or [])}

    prune_versions()
    published = max((f.stem.replace("orario-", "") for f in DATA.glob("orario-*.json")), default="")
    # Orari pubblicati da FCE che NON sono riuscito a pubblicare e che sono piu' recenti
    # (o uguali) di quello mostrato dall'app: l'app li segnala con un allarme rosso.
    pending = [{"title": p["title"], "valid_from": p.get("valid_from"), "url": p.get("url"),
                "reason": p.get("reason") or ""}
               for p in st["packages"].values()
               if p.get("status") in ("failed", "download_failed")
               and (p.get("valid_from") or italian_date(p["title"]) or "9999") >= published]
    status = {"last_check": dt.date.today().isoformat(), "errors": errors, "pending": pending}
    update_index(status=status)
    if errors and st.get("last_errors") != errors:
        notify("Orari FCE: problemi nel controllo – " + " | ".join(errors))
    st["last_errors"] = errors
    save_state(st)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    sys.exit(run(ap.parse_args().force))
