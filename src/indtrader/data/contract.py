"""Bhavcopy contract test — Phase-1 acceptance from the build plan.

Cross-checks our store's closes against NSE's OWN bhavcopy for one session. The
column contract below is NOT a guess: it is the live shape captured by the
HOME-VALIDATION round-2 probe (commit a671c97, file BhavCopy_NSE_CM_*_20261109_*,
3704 rows x 34 columns):

    TckrSymb  symbol            ClsPric      close
    PrvsClsgPric  prev close    SctySrs      series ('EQ' = cash equity)
    FinInstrmTp   instrument type ('EQI' ...)
    ISIN / FinInstrmId  identifiers (scrip-master-free ISIN map lives here)

Rows are therefore matched by SYMBOL against what the store actually holds — no
instrument-type guessing. The bhav file for session D is only published after
settlement (round 2 proved D=11-10 was NSEFileUnavailableError while 11-09 was
served), so callers must pass the store's last common session, not "yesterday".

`holidays_calendar` uses the library's live `holidays()` method (round-2 dump:
{"CBM": [{"tradingDate": "15-Jan-2026", "weekDay": ..., "description": ...}, ...]})
— dd-Mon-yyyy strings, keyed by market segment.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

BHAV_COLUMNS = {"symbol": "TckrSymb", "close": "ClsPric", "prev": "PrvsClsgPric",
                "series": "SctySrs", "instr_type": "FinInstrmTp"}


def load_bhavcopy(client, d: date) -> pd.DataFrame:
    """Fetch the equity bhavcopy for session d. The installed library returns a CSV
    PATH, not a frame (round-2 fact) — accept either, refuse loud on anything else."""
    raw = client.equity_bhavcopy(d)
    if hasattr(raw, "columns"):
        return raw
    import pathlib

    p = pathlib.Path(str(raw))
    if not p.exists():
        raise FileNotFoundError(f"bhavcopy path missing: {p}")
    return pd.read_csv(p)


def bhav_close_map(df: pd.DataFrame) -> dict[str, float]:
    """symbol -> ClsPric, contract enforced up front: the two columns the test needs
    must exist under their PROBED names, or we say so instead of returning {}
    (empty would silently pass every cross-check)."""
    missing = [c for c in (BHAV_COLUMNS["symbol"], BHAV_COLUMNS["close"]) if c not in df.columns]
    if missing:
        raise KeyError(f"bhavcopy columns missing {missing}; actual: {list(df.columns)[:12]}... "
                       "— contract changed or wrong file; refuse to guess")
    px = pd.to_numeric(df[BHAV_COLUMNS["close"]], errors="coerce")
    out: dict[str, float] = {}
    for sym, close in zip(df[BHAV_COLUMNS["symbol"]].astype(str), px):
        v = float(close) if close == close else None  # NaN check without pandas import churn
        if v is not None and sym and sym != "nan":
            out[sym.upper()] = v  # last row per symbol wins (series column can repeat; EQ dominates)
    return out


def contract_check(store, symbols: list[str], closes: dict[str, float],
                   session: str, tolerance: float = 0.01) -> dict:
    """Compare store last-close (on `session`) vs bhav. Never converts a problem
    into a pass: mismatches, missing sessions and unknown-to-bhav symbols are all
    counted and named."""
    checked, mismatched, missing = 0, [], []
    for sym in sorted(set(symbols)):
        df = store.load(sym)
        if df.empty:
            continue
        idx = pd.to_datetime(df.index).strftime("%Y-%m-%d")
        if session not in set(idx):
            missing.append(sym)
            continue
        ours = float(df["close"].loc[pd.to_datetime(df.index).strftime("%Y-%m-%d") == session].iloc[0])
        theirs = closes.get(sym.upper())
        if theirs is None:
            continue  # not in equity bhav (index/delisted/odd segment) — not evidence of error
        checked += 1
        if abs(ours - theirs) > tolerance:
            mismatched.append(f"{sym} ours={ours} bhav={theirs}")
    return {"session": session, "checked": checked, "mismatched": mismatched,
            "store_missing_session": missing}


def holidays_calendar(client, year: int) -> dict:
    """NSE trading holidays for one calendar year: {date_str -> description} from the
    CBM segment. Shape is the round-2 live dump; a different shape raises rather than
    returns empty — 'unknown != zero'."""
    data = client.holidays()
    if isinstance(data, dict) and data.get("data"):
        data = data["data"]
    if not isinstance(data, dict) or "CBM" not in data:
        raise RuntimeError(f"unexpected holidays() shape: {type(data).__name__} keys={list(data)[:6] if isinstance(data, dict) else '-'}")
    out: dict[str, str] = {}
    for row in data["CBM"]:
        d = pd.to_datetime(str(row.get("tradingDate")), format="%d-%b-%Y", errors="coerce")
        if d == d and d.year == year:  # NaT check
            out[d.strftime("%Y-%m-%d")] = str(row.get("description", ""))
    if not out:
        raise RuntimeError(f"CBM calendar parsed empty for {year} — shape drift?")
    return out
