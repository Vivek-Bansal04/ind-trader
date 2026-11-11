"""NSE trading-holiday master — stored raw, offline-readable.

Round-5 live left 5 dates in dispute: the CBM holiday rows call 2026-02-19,
03-19, 04-01, 08-26 holidays, yet 494-500 symbols have real bars with hundreds
of distinct closes there (and 2026-01-15 has stock bars but no index bar). The
`holidays` command could only answer that question LIVE through the nse lib;
this module keeps the exchange's own words IN THE STORE, so the question can
be re-answered offline, any number of times, forever.

Endpoint confirmed live 2026-11-11 (office box): GET /api/holiday-master?type=trading
-> {"CBM": [{tradingDate:"15-Jan-2026", weekDay, description, morning_session,
evening_session, Sr_no...}]}. NOTE: this is the SAME source class as the nse
lib's holidays() — it is evidence, but it is NOT an independent second opinion.
Honest scope: an independent calendar (BSE/RBI/SEBI circulars) stays on the
backlog; a false sense of agreement from the same source would be worse than
none. (An earlier draft of this module chased an "eq/makro/holiday-list" URL
that 404s — the payload memory behind it was wrong, so the module now ships
only against the confirmed shape, with a shape-drift refusal.)

The contract (mirrors everything else here):
  - THREE states: True trading / False holiday / None unknown. Unknown is never
    flattened into holiday — absence from a holiday LIST proves nothing.
  - Offline-first: fetch failure = error string, stored rows keep serving.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from indtrader.data.http import get_json, transport
from indtrader.data.store import BarStore

CAL_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
CAL_REFERER = "https://www.nseindia.com/market-data/trading-holiday"

CAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_events(
    day TEXT,
    source TEXT,                 -- 'master' (holiday-master) | 'cbm' (nse lib dump)
    is_trading INTEGER,          -- 0 = holiday row; NULL = unknown, never coerced
    label TEXT,
    payload TEXT,                -- full raw row: the audit trail
    fetched_on TEXT,
    PRIMARY KEY (day, source)
);
"""


def ensure(store) -> None:
    store.con.executescript(CAL_SCHEMA)


def _iso(d: str) -> str | None:
    """"15-Jan-2026" or ISO in -> ISO out, None on anything else (never guessed)."""
    d = str(d).strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y"):
        try:
            return date.fromisoformat(d).isoformat() if fmt == "%Y-%m-%d" \
                else pd.Timestamp(pd.to_datetime(d, format=fmt)).date().isoformat()
        except (ValueError, TypeError):
            continue
    return None


def _ingest_rows(store, rows: list, source: str) -> tuple[int, str | None]:
    """Store holiday rows (is_trading=0 + payload). -> (n, first_row_keys_note)."""
    ensure(store)
    today = date.today().isoformat()
    n = 0
    for r in rows:
        day = _iso(r.get("tradingDate", ""))
        if day is None:
            continue
        store.con.execute(
            "INSERT OR REPLACE INTO calendar_events VALUES (?,?,?,?,?,?)",
            (day, source, 0, str(r.get("description", ""))[:120],
             json.dumps(r, default=str)[:900], today))
        n += 1
    store.con.commit()
    keys = sorted(rows[0])[:12] if rows and isinstance(rows[0], dict) else None
    return n, keys


def fetch_calendar(store) -> dict:
    """Fetch the holiday master, store the CBM block verbatim.
    -> {ok, rows_stored, transport, row_keys?, error?}. Failure never clears
    stored rows and never pretends the list is empty."""
    res = {"ok": False, "rows_stored": 0, "transport": transport()}
    payload, err = get_json(CAL_URL, referer=CAL_REFERER)
    if err:
        res["error"] = err
        return res
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not (isinstance(data, dict) and isinstance(data.get("CBM"), list)):
        res["error"] = (f"unexpected shape: {type(data).__name__} "
                        f"keys={list(data)[:6] if isinstance(data, dict) else '-'}")
        return res
    rows = data["CBM"]
    if rows and not (isinstance(rows[0], dict) and "tradingDate" in rows[0]):
        res["error"] = f"row shape drift: keys={sorted(rows[0])[:12]} (confirmed has tradingDate)"
        return res
    n, keys = _ingest_rows(store, rows, "master")
    res.update(ok=True, rows_stored=n, row_keys=keys)
    return res


def store_cbm(store, hol: dict) -> int:
    """Store a CBM {date: desc} dict (from the nse lib path) under source 'cbm'
    so `holidays` runs leave their evidence behind."""
    ensure(store)
    today = date.today().isoformat()
    n = 0
    for d, desc in hol.items():
        store.con.execute(
            "INSERT OR REPLACE INTO calendar_events VALUES (?,?,?,?,?,?)",
            (str(d)[:10], "cbm", 0, str(desc)[:120], None, today))
        n += 1
    store.con.commit()
    return n


def calendar_status(store, source: str | None = None) -> tuple[str, str] | None:
    """(min_day, max_day) of stored coverage (all sources or one), None if empty.
    Holiday rows only — this is a holiday list, coverage means list span."""
    ensure(store)
    if source:
        row = store.con.execute(
            "SELECT min(day), max(day) FROM calendar_events WHERE source=?",
            [source]).fetchone()
    else:
        row = store.con.execute("SELECT min(day), max(day) FROM calendar_events").fetchone()
    return (row[0], row[1]) if row and row[0] else None


def iso_trading_day(day: str, store) -> bool | None:
    """ISO date -> False if a holiday row exists, True if a STORED NIFTY bar
    proves the session, None otherwise. Ordering matters: a bar beats a holiday
    row (what happened beats what was planned), and BOTH is the round-5
    disputed-date signature — reported by callers, not hidden here."""
    ensure(store)
    day = str(day)[:10]
    bar = store.con.execute(
        "SELECT 1 FROM candles WHERE symbol='NIFTY' AND tf='daily' AND d=? LIMIT 1",
        [day]).fetchone()
    if bar:
        return True
    row = store.con.execute(
        "SELECT is_trading FROM calendar_events WHERE day=? AND source IN ('master','cbm') "
        "LIMIT 1", [day]).fetchone()
    if row is None or row[0] is None:
        return None
    return bool(row[0])


def disputed_dates(store) -> list[str]:
    """Stored holiday rows whose date HAS a NIFTY bar — the round-5 signature,
    computable OFFLINE for every year ever fetched. This is the drift ledger."""
    ensure(store)
    rows = store.con.execute(
        "SELECT DISTINCT ce.day FROM calendar_events ce "
        "JOIN candles c ON c.d = ce.day AND c.symbol='NIFTY' AND c.tf='daily' "
        "WHERE ce.is_trading=0 ORDER BY ce.day").fetchall()
    return [r[0] for r in rows]


def nse_session_calendar(store, year: int):
    """Best ISO-date set for `year` -> (dates|None, source_label).
    Holiday rows subtracted from NIFTY bars = 'what actually traded, per both
    sources'; with no holiday rows stored it's the plain bar calendar. None
    means: say 'unknown', never assume weekdays."""
    cov = calendar_status(store)
    rows = store.con.execute(
        "SELECT DISTINCT d FROM candles WHERE symbol='NIFTY' AND tf='daily' AND d LIKE ? ORDER BY d",
        [f"{year}-%"]).fetchall()
    if rows:
        hol = {r[0] for r in store.con.execute(
            "SELECT DISTINCT day FROM calendar_events WHERE is_trading=0 AND day LIKE ?",
            [f"{year}-%"]).fetchall()}
        src = "NIFTY bars minus stored holidays" if cov else "NIFTY bars (store)"
        import pandas as pd
        return pd.DatetimeIndex([r[0] for r in rows if r[0] not in hol]), src
    return None, "no calendar source"
