"""Announcement triage for HOLDINGS ONLY (build-plan section B — low noise, catches
the career-ending events early). Keyword rules on announcement titles; anything not
matching a rule is IGNORED, not summarized — that is what keeps it signal.

Sources are probed at runtime (the NseIndiaApi method surface varies by version —
HOME-VALIDATION item 5 pins the real shape); with no live source the stored rows and
the triage rules still serve, and fetch reports the outage instead of faking empty.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from indtrader.alerts.holdings import GREEN as _HOLDINGS_GREEN
from indtrader.data.store import BarStore

ANN_SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements(
    symbol TEXT, d TEXT, title TEXT, source TEXT,
    PRIMARY KEY (symbol, d, title)
);
"""

# severity -> terms (word-boundary, case-insensitive). Order = precedence.
RED = (
    "pledge encroach", "encroachment", "forensic", "fraud", "nclt", "insolvenc",
    "bankrupt", "moratorium", "suspended from trading", "derecognition",
    "wl (blacklisted)", "blacklisted", "criminal complaint", "money laundering",
)
AMBER = (
    "resignation", "resigns", "credit rating", "downgrade", "delisting", "delist",
    # "buyback" moved to GREEN (alerts/holdings.py) on 2026-11-12 at the operator's
    # ask: for a holder it is shareholder-returning cash, not a warning. Keeping it
    # in both lists would let the AMBER triage veto every "good news" buyback
    # headline forever — the disjointness assert below makes double-listing fail
    # loudly instead of silently starving the good-news watcher.
    "right shares", "preferential issue", "qdmit", "qmmit",
    "recording of order", "upliftment", "pledge", "promoter sale", "open offer",
    "results", "board meeting",
)
DATE_KEYS = ("ANN_DATE", "ann_date", "date", "publishDate", "anndate", "timestamp")
TITLE_KEYS = ("ANN_DESC", "ann_desc", "title", "desc", "description", "announcement", "subject")

# a term may not live in both warn and good lists: warn wins in triage, so a
# double-listed term would silently disable the holdings good-news watcher for it.
assert not (set(AMBER) & set(_HOLDINGS_GREEN)), \
    f"AMBER∩GREEN overlap: {sorted(set(AMBER) & set(_HOLDINGS_GREEN))}"


@dataclass(frozen=True)
class Match:
    symbol: str
    d: str
    title: str
    severity: str  # RED | AMBER
    terms: tuple[str, ...]


def triage_title(title: str) -> tuple[str, tuple[str, ...]] | None:
    t = (title or "").lower()
    for sev, terms in (("RED", RED), ("AMBER", AMBER)):
        hits = tuple(w for w in terms if re.search(r"(?<![a-z])" + re.escape(w), t))
        if hits:
            return sev, hits
    return None


def store_announcements(store: BarStore, rows: list[dict]) -> int:
    """rows: {symbol, d, title} — idempotent upsert."""
    store.con.executescript(ANN_SCHEMA)
    n = 0
    for r in rows:
        if not r.get("title"):
            continue
        store.con.execute(
            "INSERT OR REPLACE INTO announcements VALUES (?,?,?,?)",
            (str(r["symbol"]).upper(), str(r["d"])[:10], str(r["title"]).strip()[:300], r.get("source", "nse")),
        )
        n += 1
    store.con.commit()
    return n


def fetch_announcements(symbols: list[str], store: BarStore, nse_obj=None) -> dict:
    """Probe the installed NSE lib for an announcements-ish method and store results.
    Honest failure modes: no lib -> error string; per-symbol API error -> reported."""
    if nse_obj is None:
        try:
            from nse import NSE  # NseIndiaApi >= 6
            import tempfile
            nse_obj = NSE(download_folder=tempfile.mkdtemp(prefix="nse_announce_"))
        except ImportError:
            try:
                from NseIndiaApi import NseIndia
                nse_obj = NseIndia()
            except ImportError:
                return {"ok": False, "error": "NseIndiaApi not installed (pip install 'indtrader[nse]') "
                                              "— offline announcement storage still works"}
    cands = [m for m in dir(nse_obj) if "announc" in m.lower() and not m.startswith("_")]
    if not cands:
        return {"ok": False, "error": f"no announcements method in {type(nse_obj).__name__} "
                                      "(see HOME-VALIDATION item 5 method dump)"}
    method = getattr(nse_obj, cands[0])
    stored, failed = 0, []
    for sym in symbols:
        try:
            data = method(sym)
            if isinstance(data, dict):
                data = data.get("announcements") or data.get("ann_details") or data.get("data") or []
            rows = []
            for item in data or []:
                if not isinstance(item, dict):
                    continue
                title = next((str(item[k]) for k in TITLE_KEYS if item.get(k)), "")
                d = next((str(item[k]) for k in DATE_KEYS if item.get(k)), "")
                if title:
                    rows.append({"symbol": sym, "d": d or "unknown", "title": title,
                                 "source": cands[0]})
            stored += store_announcements(store, rows)
        except Exception as e:
            failed.append(f"{sym}:{type(e).__name__}")
    return {"ok": True, "method": cands[0], "stored": stored, "failed": failed}


def triaged_recent(store: BarStore, symbols: list[str], days: int = 30) -> list[Match]:
    """Stored, triaged, RED-first, newest-first. Symbols empty => nothing (holdings-only
    by design; an empty book must produce an empty alert, never a market-wide firehose)."""
    store.con.executescript(ANN_SCHEMA)
    if not symbols:
        return []
    q = ("SELECT symbol, d, title FROM announcements WHERE symbol IN ({}) ORDER BY d DESC"
         .format(",".join("?" * len(symbols))))
    since = (date.today() - timedelta(days=int(days))).isoformat()  # stdlib: no numpy-timedelta warning path
    out: list[Match] = []
    for sym, d, title in store.con.execute(q, [s.upper() for s in symbols]).fetchall():
        m = triage_title(title)
        if m and (d == "unknown" or d >= since):
            out.append(Match(sym, d, title, m[0], m[1]))
    out.sort(key=lambda m: (m.severity != "RED", m.d), reverse=False)
    return out


def format_alert(matches: list[Match]) -> str:
    if not matches:
        return "Announcements: nothing flagged on holdings (30d) — that IS the all-clear"
    lines = [f"Announcement triage ({len(matches)} flagged):"]
    for m in matches[:20]:
        lines.append(f" [{m.severity}] {m.symbol} {m.d}: {m.title[:110]}")
    return "\n".join(lines)
