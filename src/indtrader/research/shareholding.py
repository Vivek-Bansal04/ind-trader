"""Shareholding-pattern deltas (plan §B: promoter-drop / FII-exit / pledge alerts).

Quarterly ownership per symbol, stored when seen; the value is the DELTA: promoter
stake down > 2% QoQ, notable FII exit, pledge increase — the three changes that
actually precede trouble in Indian delivery names.

Data honesty: the live NSE payload shape has NOT been inspected (office network),
so `fetch` here is probe-only: it calls the library method and STORES THE RAW JSON
plus prints the row keys — it never guesses a parser. Normalized rows get recorded
either from a shape-confirmed parser later, or by hand from the exchange filing
(`record`). Unknown shape stays unknown; it never becomes zeros.
"""
from __future__ import annotations

import json

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS shareholding(
    symbol TEXT, period TEXT,               -- 'Q1FY26'-style label or quarter-end date
    promoter REAL, fii REAL, dii REAL, public_holding REAL, pledge REAL,
    source TEXT, captured_on TEXT,
    PRIMARY KEY(symbol, period)
);
CREATE TABLE IF NOT EXISTS shareholding_raw(
    symbol TEXT, fetched_on TEXT, method TEXT, payload TEXT
);
"""

# NOTE: no candidate-key parser here on purpose. try_fetch stores the RAW payload
# and reports its row keys; the normalized table is filled only once the shape is
# CONFIRMED live (record(), or a parser added after that). Unknown stays unknown.


def ensure(store):
    store.con.executescript(SCHEMA)


def record(store, symbol: str, period: str, promoter: float | None = None, fii: float | None = None,
           dii: float | None = None, public: float | None = None, pledge: float | None = None,
           source: str = "manual") -> None:
    ensure(store)
    store.con.execute(
        "INSERT OR REPLACE INTO shareholding VALUES (?,?,?,?,?,?,?,?,?)",
        (symbol.upper(), period, promoter, fii, dii, public, pledge, source,
         pd.Timestamp.today().strftime("%Y-%m-%d")))
    store.con.commit()


def store_raw(store, symbol: str, method: str, payload) -> None:
    """Probe output lands here verbatim; parsing waits for a CONFIRMED shape."""
    ensure(store)
    store.con.execute("INSERT INTO shareholding_raw VALUES (?,?,?,?)",
                      (symbol.upper(), pd.Timestamp.today().strftime("%Y-%m-%d"), method,
                       json.dumps(payload, default=str)))
    store.con.commit()


def rows(store, symbol: str) -> pd.DataFrame:
    ensure(store)
    return pd.read_sql("SELECT * FROM shareholding WHERE symbol=? ORDER BY period",
                       store.con, params=[symbol.upper()])


def deltas(store, symbol: str) -> list[dict]:
    """QoQ changes for one symbol, oldest-first: [{period, dpromoter, dfii, dpledge, flags:[...]}].
    Only consecutive PERIODS in the table are compared — no fabricated quarters."""
    df = rows(store, symbol)
    out = []
    for i in range(1, len(df)):
        cur, prev = df.iloc[i], df.iloc[i - 1]

        def d(col):
            a, b = cur[col], prev[col]
            return None if pd.isna(a) or pd.isna(b) else round(float(a) - float(b), 2)

        dp, df_, dl = d("promoter"), d("fii"), d("pledge")
        flags = []
        if dp is not None and dp < -2.0:
            flags.append(f"promoter -{abs(dp):.2f}%")
        if df_ is not None and df_ < -3.0:
            flags.append(f"FII exit {df_:.2f}%")
        if dl is not None and dl > 0:
            flags.append(f"pledge +{dl:.2f}%")
        out.append({"period": str(cur["period"]), "prev": str(prev["period"]),
                    "dpromoter": dp, "dfii": df_, "dpledge": dl, "flags": flags})
    return out


def holding_alerts(store, symbols: list[str]) -> list[str]:
    """One-line flags for holdings with any QoQ red delta (newest quarter only)."""
    msgs = []
    for sym in sorted(set(symbols)):
        dl = deltas(store, sym)
        if dl and dl[-1]["flags"]:
            msgs.append(f"{sym} {dl[-1]['period']}: " + ", ".join(dl[-1]["flags"]))
    return msgs


def try_fetch(store, symbol: str, client=None) -> dict:
    """Probe-only live fetch: find a shareholding method, call it with cheap argument
    forms, STORE THE RAW PAYLOAD. Parses and records nothing until the shape is known;
    the printed keys are the answer the parser waits for."""
    if client is None:
        from indtrader.data.nseapi_provider import NseIndiaApiProvider
        client = NseIndiaApiProvider().client()
    cands = [m for m in dir(client) if "shareholding" in m.lower() or "shp" in m.lower()]
    if not cands:
        return {"ok": False, "error": f"no shareholding method in {type(client).__name__}"}
    last_err = None
    for m in cands:
        for args in ((symbol.upper(),), (symbol.upper(), "Quarter on Quarter"), ()):
            try:
                data = getattr(client, m)(*args)
            except TypeError as e:
                last_err = e
                continue
            except Exception as e:
                return {"ok": False, "error": f"{m}{args} -> {type(e).__name__}: {str(e)[:140]}"}
            sample = data
            if isinstance(data, dict):
                sample = data.get("data") or data
            keys = None
            if isinstance(sample, list) and sample and isinstance(sample[0], dict):
                keys = sorted(sample[0])
            store_raw(store, symbol, m, data)
            return {"ok": True, "method": m, "stored_raw": True,
                    "row_keys": keys,
                    "note": "raw payload stored; parser waits for a CONFIRMED shape "
                            "(no guessing — see module docstring)",
                    "last_err": None if not last_err else str(last_err)[:120]}
    return {"ok": False, "error": f"tried {cands}: {str(last_err)[:140]}"}
