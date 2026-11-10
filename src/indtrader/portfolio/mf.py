"""Investing leg (plan Phase 6): MF NAV tracking off AMFI's own NAVAll.txt.

AMFI (the industry's official NAV publisher) is reachable even where NSE/Yahoo are
blocked, and its file shape is verifiable — so this parses only after checking the
header contract, and refuses rows that don't match (unknown != zero, as everywhere).
No mftool dependency: the XIRR engine here is already the plan's "own implementation".

Flow conventions: SIP instalments are ledger rows with source='mf', fractional units
allowed; scheme codes are prefixed 'MF:' so they never collide with exchange symbols.
Tax note: the FIFO/tax engine is EQUITY-only. Equity-oriented funds broadly follow the
same rules; debt funds are taxed at slab rates (Apr-2023 rules) — the rebalance tax lens
flags that instead of guessing. Computation, not advice.
"""
from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd

NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
# REAL header, verified against the live file (2026-11-10) by the probe test —
# if AMFI ever changes it again, parse raises instead of guessing positions.
HEADER = ("Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;"
          "Plan;Option;Net Asset Value;Date")
MF_PREFIX = "MF:"
NAME_KEYS = ("name", "schemename", "scheme")


def is_mf(symbol: str) -> bool:
    return str(symbol).upper().startswith(MF_PREFIX)


def strip_prefix(symbol: str) -> str:
    return str(symbol).upper()[len(MF_PREFIX):] if is_mf(symbol) else str(symbol).upper()


def parse_nav_all(text: str) -> pd.DataFrame:
    """Strict contract parse: header must equal the live-verified shape (the probe
    test re-checks this monthly), data rows must have 8 fields with numeric code +
    parseable date + numeric nav — otherwise refuse. A mis-parsed NAV poisons every
    downstream number; unknown != zero applies to file shapes too."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines or ";" not in lines[0]:
        raise ValueError("NAVAll: no semicolon header found — endpoint shape changed, refusing to guess")
    if lines[0].replace(" ", "") != HEADER.replace(" ", ""):
        raise ValueError(f"NAVAll header changed, not parsing blindly: {lines[0][:120]!r}")
    rows, bad = [], 0
    for ln in lines[1:]:
        if ";" not in ln:
            continue  # publisher banner/family lines ("Axis Mutual Fund") — structure, not garbage
        parts = ln.split(";")
        if len(parts) != 8:
            bad += 1
            continue
        code, name, nav_s, d_s = parts[0].strip(), parts[3].strip(), parts[6].strip(), parts[7].strip()
        if not code.isdigit():
            bad += 1
            continue
        try:  # NAVAll dates are dd-Mon-yyyy; ISO accepted too (ETF files use it)
            d = date.fromisoformat(d_s)
        except ValueError:
            try:
                from datetime import datetime

                d = datetime.strptime(d_s, "%d-%b-%Y").date()
            except ValueError:
                bad += 1
                continue
        try:
            nav = float(nav_s)
        except ValueError:
            bad += 1
            continue
        rows.append((code, name, str(d), nav))
    if not rows:
        raise ValueError("NAVAll: zero parseable rows — treat as outage, not as empty")
    df = pd.DataFrame(rows, columns=["scheme_code", "name", "d", "nav"])
    df.attrs["bad_rows"] = bad  # malformed lines — reported, never silently absorbed
    return df.sort_values(["scheme_code", "d"]).drop_duplicates("scheme_code", keep="last")


def fetch_navs(store) -> dict:
    """Download NAVAll.txt, contract-check, store latest NAVs. Honest failure dict;
    never raises past here, never stores partial garbage (parse first, store after)."""
    import httpx

    store.con.executescript(MF_SCHEMA)
    try:
        r = httpx.get(NAV_URL, timeout=30.0, follow_redirects=True)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code} from AMFI"}
        df = parse_nav_all(r.text)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:180]}"}
    df = df.sort_values("d").drop_duplicates("scheme_code", keep="last")
    nav_date = str(df["d"].max())
    rows = [(str(row["scheme_code"]), row["name"], float(row["nav"]), nav_date, "amfi")
            for _, row in df.iterrows()]
    store.con.executemany("INSERT OR REPLACE INTO mf_navs VALUES (?,?,?,?,?)", rows)
    store.con.commit()
    return {"ok": True, "stored": len(rows), "nav_date": nav_date,
            "dropped_malformed": int(df.attrs.get("bad_rows", 0))}


MF_SCHEMA = """
CREATE TABLE IF NOT EXISTS mf_navs(
    scheme_code TEXT PRIMARY KEY, name TEXT, nav REAL, d TEXT, source TEXT
);
"""


def store_nav(store, scheme_code: str, name: str, nav: float, d: str, source: str = "manual") -> None:
    store.con.executescript(MF_SCHEMA)
    store.con.execute("INSERT OR REPLACE INTO mf_navs VALUES (?,?,?,?,?)",
                      (str(scheme_code), name, float(nav), str(d), source))
    store.con.commit()


def mf_mark(store, symbol: str) -> float | None:
    """Latest stored NAV for an MF: symbol, or None (unknown — caller must not guess).
    Non-MF symbols return None too: this store is not the equity price source."""
    if not is_mf(symbol):
        return None
    store.con.executescript(MF_SCHEMA)
    r = store.con.execute("SELECT nav FROM mf_navs WHERE scheme_code=?",
                          [strip_prefix(symbol)]).fetchone()
    return float(r[0]) if r else None


def resolve_scheme(store, text: str) -> list[tuple[str, str]]:
    """Substring lookup against stored names/codes — for humans who don't memorize
    scheme codes. Empty list means: fetch mf-navs first, or refine the text."""
    store.con.executescript(MF_SCHEMA)
    like = f"%{re.sub(r'[%_]', '', text.upper())}%"
    rows = store.con.execute(
        "SELECT scheme_code, name FROM mf_navs WHERE UPPER(name) LIKE ? OR scheme_code LIKE ? "
        "ORDER BY name LIMIT 10", [like, like]).fetchall()
    return [(r[0], r[1]) for r in rows]


def mf_flows(fills) -> list:
    """Ledger entries that are MF flows, for the combined XIRR view."""
    return [f for f in fills if is_mf(f.symbol)]


def equity_flows(fills) -> list:
    return [f for f in fills if not is_mf(f.symbol)]
