"""Corporate actions (splits/bonuses/dividends) — probe-only until shape confirmed.

WHY this module exists: round-4 live proved yfinance adjustment factors are
dividend-only — the HDFCBANK 1:2 split cliff survives `adjusted=True`, and the
round-5 split-gap check named 59 such symbols. The cure needs a real split
source; NSE's own corporate-action records are it.

Probe v2 (after round-5): the first probe called `actions` and got an EMPTY
list — "payload is not a row list (got list)" literally means `[]`, and a round
of guessing one payload was the bug, not the discipline. So the probe now tries
EVERY corporate-actions-ish method with EVERY cheap argument form, stores each
non-empty payload RAW (with a replay path: `coractions SYM --from-raw`), and
prints a per-call shape report — row_keys for dicts, or `list of <X>` /
`empty list` when there are no dict rows to learn from. parse_events accepts
ONLY the confirmed-row shape (round-5's pattern-row spelling); every other
shape refuses loudly. When the paste lands, only the key names change — the
refusal logic never does.
"""
from __future__ import annotations

import json
import re

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS corporate_actions_raw(
    symbol TEXT, fetched_on TEXT, method TEXT, payload TEXT
);
"""

# Confirmed-row keys live HERE once seen live; until then parse refuses.
# Round-4's shareholding lesson: NSE pattern rows spell dates "17-Nov-2025" and
# purposes as free text ("Split of shares"); those spellings are *candidates*
# until this endpoint's own payload says so.
CA_KEY_CANDS = {
    "date": ("date", "exDate", "EX_Date", "recordDate", "crsDate", "from"),
    "type": ("purpose", "compulsory", "desc", "subject", "type"),
    "ratio": ("ratio", "crsRatio", "crsRatioTo", "faceValueChange", "splitRatio"),
}


def _split_ratio(subject: str) -> float | None:
    """Guess split/bonus ratio from subject text.

    Returns the STORED FACTOR (1 / price_multiplier), for forward-fill +
    cumulative product in combined_factors():
      - 1:2 split -> 2.0 (price halves → stored factor 2.0)
      - Bonus 1:1 -> 2.0 (shares double → stored factor 2.0)
      - Face value 2→1 -> 2.0 (shares double → stored factor 2.0)
      - dividend -> None
    """
    s = subject.lower()
    import re

    is_bonus = "bonus" in s
    is_split_or_fv = bool(re.search(r'\b(split|sub\.?div|face\s*val|fv)\b', s))

    if not (is_bonus or is_split_or_fv):
        return None

    # Split/bonus ratio: "Bonus 1:1", "Stock Split 2:1", "1:2"
    m = re.search(r'(\d+)\s*[:]\s*(\d+)', s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if is_bonus:
            # Bonus X:Y: for every X shares, get Y new → total X+Y → price × X/(X+Y)
            # stored factor = (X+Y)/X  (even when X==Y, shares double)
            return round((a + b) / a, 6)
        else:
            # Split X:Y: a old shares become b new → price × a/b
            # stored factor = b/a  (skip 1:1 → no change)
            if a == b:
                return None
            return round(b / a, 6)

    # Face-value change: "From Rs 2 To Rs 1", "From Rs.10/- To Rs.2/-"
    if re.search(r'\b(fv|face\s*val|face value|sub\.?div|split)\b', s):
        m = re.search(r'(\d+)\D{0,50}?\bto\b\D{0,50}?(\d+)', s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a == b:
                return None
            # Face value A→B: shares scale by A/B, price × B/A
            # stored factor = A/B
            return round(a / b, 6)

    return None


def ensure(store) -> None:
    store.con.executescript(SCHEMA)


def store_raw(store, symbol: str, method: str, payload) -> None:
    ensure(store)
    store.con.execute("INSERT INTO corporate_actions_raw VALUES (?,?,?,?)",
                      (symbol.upper(), pd.Timestamp.today().strftime("%Y-%m-%d"), method,
                       json.dumps(payload, default=str)))
    store.con.commit()


def raw_payloads(store, symbol: str) -> list[tuple[str, str, object]]:
    """Stored (fetched_on, method, payload) newest-first — for --from-raw replay."""
    ensure(store)
    return [(r[0], r[1], json.loads(r[2])) for r in store.con.execute(
        "SELECT fetched_on, method, payload FROM corporate_actions_raw "
        "WHERE symbol=? ORDER BY rowid DESC", [symbol.upper()]).fetchall()]


def payload_shape(payload) -> str:
    """One-line shape report — what the probe output teaches the parser."""
    if isinstance(payload, dict):
        d = payload.get("data", payload)
        if isinstance(d, list):
            if not d:
                return "dict.data: empty list"
            if isinstance(d[0], dict):
                return f"dict.data: {len(d)} rows, keys={sorted(d[0])[:12]}"
            return f"dict.data: {len(d)} entries of {type(d[0]).__name__}"
        return f"dict keys={sorted(payload)[:12]}"
    if isinstance(payload, list):
        if not payload:
            return "empty list"
        if isinstance(payload[0], dict):
            return f"list: {len(payload)} rows, keys={sorted(payload[0])[:12]}"
        return f"list: {len(payload)} entries of {type(payload[0]).__name__}"
    return f"{type(payload).__name__}"


def upsert_corporate_actions(store, symbol: str, events: list[dict]) -> int:
    """Store parsed corporate-action events in the normalized table."""
    store.con.executescript(SCHEMA)
    rows = [(symbol.upper(), e["date"], e["kind"], e.get("ratio")) for e in events
            if e.get("date") and e.get("kind")]
    store.con.executemany(
        "INSERT OR REPLACE INTO corporate_actions VALUES (?,?,?,?)", rows)
    store.con.commit()
    return len(rows)


def build_split_factors(store, symbol: str, events: list[dict]) -> int:
    """Build split_factors from parsed events that have a ratio (splits/bonuses)."""
    store.con.executescript(SCHEMA)
    split_factors = {}
    for e in events:
        ratio = _split_ratio(e["kind"])
        if ratio is not None:
            split_factors[e["date"]] = ratio
    return store.upsert_split_factors(symbol, split_factors)


def parse_events(symbol: str, payload) -> list[dict]:
    """Confirmed-shape-only parse: -> [{date, kind, ratio, symbol}]. Raises naming
    the tried key spellings AND the actual row keys (probe first, never guess)."""
    sample = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not (isinstance(sample, list) and sample and isinstance(sample[0], dict)):
        raise ValueError(f"payload is not a row list ({payload_shape(payload)})")
    out = []
    for r in sample:
        got = {}
        for field, cands in CA_KEY_CANDS.items():
            for k in cands:
                if k in r and r[k] not in (None, ""):
                    got[field] = str(r[k])
                    break
            else:
                raise KeyError(f"{field} not found (tried {list(cands)}); actual row keys: "
                               f"{sorted(r)[:14]} — refuse to guess")
        out.append({"date": got["date"], "kind": got["type"],
                    "ratio": got.get("ratio"), "symbol": symbol.upper()})
    return out


def is_split_kind(kind: str) -> bool:
    """Split/bonus classifier: only words seen in NSE pattern rows. Ambiguous
    purposes are NOT splits — they're never silently used as factors."""
    return bool(re.search(r"split|bonus", str(kind), re.I))


def _emptyish(data) -> bool:
    """Empty means EMPTY — not 'unconfirmed shape'. Drives which calls get stored."""
    if data is None:
        return True
    if isinstance(data, dict):
        return not (data.get("data") or data)
    if isinstance(data, list):
        return not data
    return False


def try_fetch(store, symbol: str, client=None) -> dict:
    """Probe v2: try every corporate-actions-ish method × arg form, store each
    non-empty payload, report every call's shape. One method returning [] proves
    nothing; the inventory is the answer."""
    if client is None:
        from indtrader.data.nseapi_provider import NseIndiaApiProvider
        client = NseIndiaApiProvider().client()
    cands = [m for m in dir(client)
             if "corporate" in m.lower() or "ca_" in m.lower() or "action" in m.lower()]
    if not cands:
        return {"ok": False, "error": f"no corporate-actions method in {type(client).__name__}"}
    sym = symbol.upper()
    forms = ((sym,), (sym, "cm"), (sym, "F&O"), ())
    probed, stored_any = [], False
    for m in cands:
        # actions() needs option_type="equities" first positional arg
        if m == "actions":
            arg_forms = [("equities",)]
        else:
            arg_forms = [(sym,), (sym, "cm"), (sym, "F&O"), ()]
        for args in arg_forms:
            try:
                data = getattr(client, m)(*args)
            except TypeError:
                continue  # wrong arg form for this signature — try the next form
            except Exception as e:
                probed.append(f"{m}{args}: {type(e).__name__}: {str(e)[:90]}")
                break  # this method's endpoint itself failed — don't hammer it
            shape = payload_shape(data)
            note = shape
            try:
                events = parse_events(sym, data)
                stored_events = upsert_corporate_actions(store, sym, events)
                stored_factors = build_split_factors(store, sym, events)
                note += (f" -> PARSED {len(events)} events; "
                         f"{stored_events} stored, {stored_factors} split factors")
            except (KeyError, ValueError):
                pass  # unconfirmed shape: the shape report IS the deliverable
            probed.append(f"{m}{args}: {note}")
            if not _emptyish(data):
                store_raw(store, sym, f"{m}{args}", data)
                stored_any = True
                break  # rows found for this method — other arg forms rarely add more
    if not stored_any:
        return {"ok": False, "probed": probed,
                "error": "every corporate-actions call returned empty/err — "
                         "paste this inventory, it decides the next move"}
    return {"ok": True, "stored_any": True, "probed": probed,
            "next": "coractions " + sym + " --from-raw  (replay parser over stored payloads)"}

