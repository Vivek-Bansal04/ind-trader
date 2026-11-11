"""Corporate actions (splits/bonuses/dividends) — probe-only until shape confirmed.

WHY this module exists: round-4 live proved yfinance adjustment factors are
dividend-only — the HDFCBANK 1:2 split cliff survives `adjusted=True`. The
health split-gap check now NAMES those symbols; this module is the path to the
cure: NSE's own corporate-action records carry the split ratio, from which a
real daily factor series can be built.

Same honesty lifecycle as shareholding (round 3 probed, round 4 confirmed,
round 4 shipped the parser): fetch STORES THE RAW PAYLOAD and prints row keys —
it never guesses a parser. `parse_events` is written but refuses until its
expected keys are confirmed live; when the probe output shows the real shape,
only the key names change, the refusal logic stays.
"""
from __future__ import annotations

import json

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS corporate_actions_raw(
    symbol TEXT, fetched_on TEXT, method TEXT, payload TEXT
);
"""

# Expected shape per the docs of the library's corporate-actions methods. If the
# live payload uses other names, parse_events refuses and the probe output tells
# us the real ones — the parser then adapts (like shareholding's did), the
# refusal NEVER becomes a guess.
CA_KEY_CANDS = {
    "date": ("exDate", "EX_Date", "date", "exDate1"),
    "type": ("purpose", "compulsory", "Corp Actions", "type"),
}


def ensure(store) -> None:
    store.con.executescript(SCHEMA)


def store_raw(store, symbol: str, method: str, payload) -> None:
    ensure(store)
    store.con.execute("INSERT INTO corporate_actions_raw VALUES (?,?,?,?)",
                      (symbol.upper(), pd.Timestamp.today().strftime("%Y-%m-%d"), method,
                       json.dumps(payload, default=str)))
    store.con.commit()


def parse_events(symbol: str, payload) -> list[dict]:
    """Confirmed-shape-only parse: -> [{date, kind, ratio_guess}]. Raises naming
    the tried key spellings when the payload shape is unknown (probe first!)."""
    sample = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not (isinstance(sample, list) and sample and isinstance(sample[0], dict)):
        raise ValueError(f"payload is not a row list (got {type(sample).__name__})")
    out = []
    for r in sample:
        got = {}
        for field, cands in CA_KEY_CANDS.items():
            for k in cands:
                if k in r and r[k] is not None:
                    got[field] = str(r[k])
                    break
            else:
                raise KeyError(f"{field} not found (tried {list(cands)}); actual row keys: "
                               f"{sorted(r)[:14]} — refuse to guess")
        out.append({"date": got["date"], "kind": got["type"], "symbol": symbol.upper()})
    return out


def try_fetch(store, symbol: str, client=None) -> dict:
    """Probe-only live fetch: find a corporate-actions method, call it with cheap
    argument forms, store the RAW payload, print the row keys. Parsing waits for
    the confirmed shape (see module docstring)."""
    if client is None:
        from indtrader.data.nseapi_provider import NseIndiaApiProvider
        client = NseIndiaApiProvider().client()
    cands = [m for m in dir(client)
             if "corporate" in m.lower() or "ca_" in m.lower() or "action" in m.lower()]
    if not cands:
        return {"ok": False, "error": f"no corporate-actions method in {type(client).__name__}"}
    last_err = None
    for m in cands:
        for args in ((symbol.upper(),), (symbol.upper(), "cm"), ()):
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
            try:
                events = parse_events(symbol, data)
                note = f"{len(events)} events parsed; raw also stored"
            except (KeyError, ValueError) as e:
                note = f"raw stored; parse REFUSED: {e}"
                events = None
            return {"ok": True, "method": m, "stored_raw": True, "row_keys": keys,
                    "events": events, "note": note,
                    "last_err": None if not last_err else str(last_err)[:120]}
    return {"ok": False, "error": f"tried {cands}: {str(last_err)[:140]}"}
