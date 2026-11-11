"""Shareholding-pattern deltas (plan §B: promoter-drop / FII-exit / pledge alerts).

Quarterly ownership per symbol, stored when seen; the value is the DELTA: promoter
stake down > 2% QoQ, notable FII exit, pledge increase — the three changes that
actually precede trouble in Indian delivery names.

Data honesty: the shape was confirmed LIVE on 2026-11-11 (round-4 full key dump):
quarterly PATTERN rows with date / pr_and_prgrp / public_val — parsed by
`parse_pattern_rows`. FII/DII/pledge are NOT in that payload, so the live path
leaves them NULL (deltas skip NULLs); those columns can still be filled by hand
from the exchange filing (`record`). Unknown shape stays unknown; it never
becomes zeros.
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


# ---- confirmed-shape parser (round-4 live payload, 2026-11-11) --------------------
# The raw rows are the quarterly shareholding-PATTERN summary: one row per quarter
# (23 rows = 23 quarters), NOT the detailed per-holder breakdown. Confirmed keys:
#   date          quarter-end, "30-SEP-2026"
#   pr_and_prgrp  promoter + promoter-group % ("50.44")
#   public_val    public %  ("49.56")
#   employeeTrusts, desc ("NEW_1"), symbol, isin
# NOT present in this shape: FII / DII / pledge columns. Those stay unknown —
# record() can still fill them by hand, and the live side leaves them NULL rather
# than inventing zeros. If the payload shape changes, parse_rows refuses loudly.
SH_PATTERN_KEYS = {"date": "quarter-end", "pr_and_prgrp": "promoter%",
                   "public_val": "public%"}


def quarter_label(d: str) -> str:
    """"30-SEP-2026" -> "Q3FY27" (Indian FY starts April; quarter = calendar Q+1
    across the FY boundary). Parse failures raise — never store a guessed label."""
    from datetime import datetime

    dt = datetime.strptime(d, "%d-%b-%Y")
    fy = dt.year + 1 if dt.month >= 4 else dt.year
    q = {3: 4, 6: 1, 9: 2, 12: 3}[dt.month]  # mar=Q4 of FY, jun=Q1 of next FY...
    return f"Q{q}FY{str(fy)[2:]}"


def parse_pattern_rows(symbol: str, payload) -> list[dict]:
    """Confirmed-shape parse: -> [{period, promoter, public}]. Strict: every key in
    SH_PATTERN_KEYS must exist on a row or the row raises. Rows for a different
    symbol are dropped (the payload can carry peers)."""
    sample = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not (isinstance(sample, list) and sample and isinstance(sample[0], dict)):
        raise ValueError(f"payload is not a row list (got {type(sample).__name__})")
    out = []
    for r in sample:
        missing = [k for k in SH_PATTERN_KEYS if k not in r]
        if missing:
            raise KeyError(f"row keys missing {missing}; confirmed shape has "
                           f"{sorted(SH_PATTERN_KEYS)} — payload changed, refuse to guess")
        if str(r.get("symbol", symbol)).upper() != symbol.upper():
            continue
        try:
            promoter = float(r["pr_and_prgrp"])
            public = float(r["public_val"])
        except (TypeError, ValueError):
            continue  # placeholder rows ("-", null) are not data — skip, don't zero
        out.append({"period": quarter_label(r["date"]), "promoter": promoter,
                    "public": public})
    if not out:
        raise ValueError(f"no usable rows for {symbol} among {len(sample)} rows")
    return out


def ingest_pattern_payload(store, symbol: str, payload) -> int:
    """Parse + record the confirmed-shape rows; returns count stored."""
    n = 0
    for r in parse_pattern_rows(symbol, payload):
        record(store, symbol, r["period"], promoter=r["promoter"], public=r["public"],
               source="nse-shareholding")
        n += 1
    return n


def try_fetch(store, symbol: str, client=None) -> dict:
    """Live probe v2 (round-7): sweep EVERY shareholding-ish method × arg form,
    store each non-empty payload RAW, and ingest the confirmed quarterly-summary
    shape wherever it appears. Round-5 lesson: a one-shot arg-form lottery
    landed on the per-holder DETAIL view and (correctly) refused — but the
    right answer is to keep probing and take the rows from whichever payload
    genuinely carries the confirmed shape, reporting every form seen."""
    from indtrader.data.payloads import payload_shape

    if client is None:
        from indtrader.data.nseapi_provider import NseIndiaApiProvider
        client = NseIndiaApiProvider().client()
    cands = [m for m in dir(client) if "shareholding" in m.lower() or "shp" in m.lower()]
    if not cands:
        return {"ok": False, "error": f"no shareholding method in {type(client).__name__}"}
    sym = symbol.upper()
    forms = ((sym,), (sym, "Quarter on Quarter"), (sym, "Company"),
             (sym, "Promoters"), (sym, "Institutions"), ())
    probed, quarters, raws = [], 0, 0
    for m in cands:
        meth = getattr(client, m)
        for args in forms:
            label = f"{m}({', '.join(map(repr, args))})"
            try:
                data = meth(*args)
            except TypeError as e:
                probed.append(f"{label}: signature-miss {str(e)[:60]}")
                continue  # wrong arg form for this signature — next form
            except Exception as e:
                probed.append(f"{label}: {type(e).__name__}: {str(e)[:90]}")
                break  # the endpoint itself failed — don't hammer it
            empty = (data is None
                     or (isinstance(data, dict) and not (data.get("data") or data))
                     or (isinstance(data, list) and not data))
            if empty:
                probed.append(f"{label}: {payload_shape(data)}")
                continue
            store_raw(store, sym, label, data)
            raws += 1
            try:
                n = ingest_pattern_payload(store, sym, data)
                quarters += n
                probed.append(f"{label}: {payload_shape(data)} -> CONFIRMED shape, "
                              f"{n} quarters recorded")
                break  # the right view for this method; later forms are the same data restated
            except (KeyError, ValueError) as e:
                probed.append(f"{label}: {payload_shape(data)} -> not the confirmed "
                              f"summary ({str(e)[:70]})")
    ok = quarters > 0
    return {"ok": ok, "quarters_recorded": quarters, "raws_stored": raws, "probed": probed,
            "note": (f"{quarters} quarters from the confirmed shape" if ok else
                     "no payload carried the confirmed summary shape — raws stored, "
                     "the probed inventory decides the next move")}
