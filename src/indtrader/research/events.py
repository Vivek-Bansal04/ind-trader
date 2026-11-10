"""Event calendar (results dates) + the size rule the plan mandates:
no fresh entry within 2 sessions of earnings, half size within 5.

Dates come from the live NSE lib when reachable (probe pattern, same as announce.py;
HOME-VALIDATION item 5 pins the shape) and are stored locally afterwards. Unknown
event date => NO restriction applied but the unknown is stated — absence of data
never silently blocks trades, and never silently permits an unguarded one either:
the note rides along to the human.
"""
from __future__ import annotations

import pandas as pd

from indtrader.data.store import BarStore
from indtrader.research.calendar import index_sessions

EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    symbol TEXT, d TEXT, kind TEXT, source TEXT,
    PRIMARY KEY (symbol, d, kind)
);
"""

NO_ENTRY_WITHIN = 2   # sessions
HALF_SIZE_WITHIN = 5  # sessions
DATE_KEYS = ("aDate", "atDate", "date", "eventDate", "d", "EXCH_DATE")


def store_events(store: BarStore, rows: list[dict]) -> int:
    store.con.executescript(EVENTS_SCHEMA)
    n = 0
    for r in rows:
        if r.get("d"):
            store.con.execute("INSERT OR REPLACE INTO events VALUES (?,?,?,?)",
                              (str(r["symbol"]).upper(), str(r["d"])[:10],
                               r.get("kind", "results"), r.get("source", "nse")))
            n += 1
    store.con.commit()
    return n


def fetch_results_calendar(store: BarStore, nse_obj=None) -> dict:
    """Best-effort probe for a results-calendar method; failures are reported, not swallowed."""
    if nse_obj is None:
        try:
            from nse import NSE
            import tempfile
            nse_obj = NSE(download_folder=tempfile.mkdtemp(prefix="nse_events_"))
        except ImportError:
            try:
                from NseIndiaApi import NseIndia
                nse_obj = NseIndia()
            except ImportError:
                return {"ok": False, "error": "NseIndiaApi not installed — stored events still work"}
    cands = [m for m in dir(nse_obj) if ("result" in m.lower() and "calendar" in m.lower())
             or "earnings" in m.lower()]
    if not cands:
        return {"ok": False, "error": f"no results-calendar method in {type(nse_obj).__name__}"}
    try:
        data = getattr(nse_obj, cands[0])()
        if isinstance(data, dict):
            data = data.get("data") or []
        rows = []
        for item in data or []:
            if isinstance(item, dict):
                sym = next((str(item[k]) for k in ("symbol", "Symbol", "company", "cm_scrip") if item.get(k)), "")
                d = next((str(item[k]) for k in DATE_KEYS if item.get(k)), "")
                if sym and d:
                    rows.append({"symbol": sym, "d": d, "kind": "results", "source": cands[0]})
        return {"ok": True, "method": cands[0], "stored": store_events(store, rows)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}


def next_event(store: BarStore, symbol: str, today: pd.Timestamp | None = None) -> str | None:
    store.con.executescript(EVENTS_SCHEMA)
    today = today or pd.Timestamp.today().normalize()
    r = store.con.execute(
        "SELECT MIN(d) FROM events WHERE symbol=? AND kind='results' AND d >= ?",
        [symbol.upper(), today.strftime("%Y-%m-%d")]).fetchone()
    return r[0] if r and r[0] else None


def sessions_until(store: BarStore, symbol: str, index_symbol: str = "NIFTY",
                   today: pd.Timestamp | None = None) -> int | None:
    """Sessions from today to the next stored results event; None if unknown."""
    d = next_event(store, symbol, today)
    if not d:
        return None
    idx = index_sessions(store.load(index_symbol))
    if len(idx) == 0:
        return None  # unknown calendar -> unknown, not "far away"
    today = today or pd.Timestamp.today().normalize()
    return int(((idx >= today) & (idx <= pd.Timestamp(d))).sum()) - 1


def size_gate(store: BarStore, symbol: str, qty: int, index_symbol: str = "NIFTY",
              today: pd.Timestamp | None = None) -> tuple[int, str]:
    """(allowed_qty, note) implementing the plan's event rule. Unknown event ->
    unrestricted but flagged, so the human sees the blind spot."""
    s = sessions_until(store, symbol, index_symbol, today)
    if s is None:
        return qty, "results date unknown — no event guard active (fetch the calendar)"
    if s < 0:  # event date not in session list (holiday mismatch): treat by calendar distance
        d = next_event(store, symbol, today)
        days = (pd.Timestamp(d) - (today or pd.Timestamp.today().normalize())).days
        s = max(0, round(days * 5 / 7))
    if s <= NO_ENTRY_WITHIN:
        return 0, f"results in {s} session(s): NO fresh entry (plan rule)"
    if s <= HALF_SIZE_WITHIN:
        return max(1, qty // 2), f"results in {s} session(s): half size (plan rule)"
    return qty, f"next results in {s} sessions"
