"""Corporate actions (splits/bonuses/dividends) — the split-factor cure.

WHY this module exists: round-4 live proved yfinance adjustment factors are
dividend-only — the HDFCBANK 1:2 split cliff survives `adjusted=True`, and the
round-5 split-gap check named 59 such symbols. NSE's own corporate-action
records are the split source; store.combined_factors merges the two feeds.

Probe discipline (round-5 lesson): the first probe's single `actions()` answer
was an EMPTY list — one payload was too thin to build on, so try_fetch probes
EVERY corporate-actions-ish method with EVERY plausible argument form (the
laptop's claimed `option_type="equities"` form is tried FIRST, then the old
forms as fallback, and even TypeErrors are REPORTED — a signature guess that
fails must be visible, not silently skipped). Each non-empty payload is stored
RAW; parse_events accepts only confirmed row keys and otherwise refuses,
naming both what it tried and what it saw.

Ratio convention (round-5 review): ONE rule, the min:max reading — a split
into more pieces always reduces price (1:2 split -> 0.5, bonus 1:1 -> 0.5,
face value 10->2 -> 0.2). The first version mixed split=b/a with
face-value=a/b into one cumprod, which silently un-adjusts one branch. No
usable numbers -> None -> reported as UNRESOLVED, never a silent no-op: the
split-gap health check will keep naming the symbol until a ratio lands.
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

# CONFIRMED LIVE 2026-11-11 (round-6 PASTE-4): actions("equities", SYMBOL) ->
# list rows with keys ['bcEndDate','bcStartDate','caBroadcastDate','comp','exDate',
# 'faceVal','ind','isin','ndEndDate','ndStartDate','recDate','series','subject',
# 'symbol']. The RATIO lives in faceVal + bcStartDate: the face-value step
# (previous face value -> new) — HDFCBANK's three rows (10->2 2011, 10->5 2019,
# 5->2.5 2025) reproduce its known 5x/2x/2x history exactly. subject carries the
# text kind ("Bonus 1:1", "Face Value Split...").
CA_KEY_CANDS = {
    "date": ("exDate", "date", "EX_Date", "recordDate", "crsDate", "from"),
    "type": ("subject", "purpose", "compulsory", "desc", "type"),
}
# Optional fields: absent is NORMAL (the confirmed payload has no ratio column
# at all), so a missing one is never a refusal — unlike date/type, whose absence
# means the shape changed and must be reported.
CA_OPTIONAL_KEYS = ("ratio", "crsRatio", "crsRatioTo", "faceValueChange", "splitRatio")


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


def _split_ratio(subject: str) -> float | None:
    """Price multiplier a split/bonus/face-value-cut imposes on the share price,
    or None for 'not a split' / 'no usable numbers' (dividends, garbage).

    ONE convention: a split into more pieces always REDUCES the price.
    Bonus text is arithmetic ("bonus a:b" = b new shares per a held -> a/(a+b),
    so the usual 1:1 bonus -> 0.5); split notation direction varies ("1:2" or
    "2:1"), so the price effect is read as the shrink, min:max (an equal split
    is a no-op -> None). Round-5 review killed the first version, which mixed
    split=b/a with face-value=a/b into one cumprod — that silently un-adjusted
    one branch. No usable numbers returns None: unconfirmed is never guessed,
    and build_split_factors REPORTS the symbol rather than silently no-op'ing
    a real split.
    """
    s = str(subject).lower()
    if not re.search(r"bonus|split|sub\s*div|sub-div|face\s*val", s):
        return None
    is_bonus = "bonus" in s
    m = re.search(r"(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)", s)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if a <= 0 or b <= 0:
            return None
        if is_bonus:  # bonus a:b = b new shares per a held -> price * a/(a+b)
            return round(a / (a + b), 6)
        # split notation direction varies (1:2 vs 2:1) — the price effect is
        # always the shrink, so read min:max; an equal split IS a no-op.
        return round(min(a, b) / max(a, b), 6) if a != b else None
    if is_bonus and re.search(r"bonus\s+\d", s):  # "Bonus 1" = one new per held
        return 0.5
    m = re.search(r"(?:rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(?:/-)?\s*to\s*(?:rs\.?|inr)?\s*(\d+(?:\.\d+)?)", s)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if a <= 0 or b <= 0 or a == b:
            return None
        return round(min(a, b) / max(a, b), 6)
    return None


def upsert_corporate_actions(store, symbol: str, events: list[dict]) -> int:
    """Store parsed corporate-action events. The ratio column holds the PARSED
    price multiplier when the subject text stated one — never the raw faceVal,
    whose meaning (before/after the action) the payload does not say."""
    ensure(store)
    rows = []
    for e in events:
        if not (e.get("date") and e.get("kind")):
            continue
        ratio = e.get("ratio")
        if ratio in (None, ""):
            ratio = _split_ratio(e["kind"]) if is_split_kind(e["kind"]) else None
        rows.append((symbol.upper(), str(e["date"])[:40], str(e["kind"])[:300], ratio))
    store.con.executemany(
        "INSERT OR REPLACE INTO corporate_actions VALUES (?,?,?,?)", rows)
    store.con.commit()
    return len(rows)


def build_split_factors(store, symbol: str, events: list[dict]) -> tuple[int, list[str]]:
    """Store split factors from parsed split/bonus events. -> (n_stored, unresolved)
    where unresolved = events that LOOK like splits/bonuses but carry no
    parseable ratio — reported, never silently dropped. Dates normalised to ISO
    ("26-Aug-2025" -> 2025-08-26); an unparseable date raises — a split whose
    date cannot be read must stop the parse, not shift the factor a few months."""
    ensure(store)
    factors, unresolved = {}, []
    for e in events:
        if not is_split_kind(e["kind"]):
            continue
        ratio = _split_ratio(e["kind"])
        if ratio is None:
            unresolved.append(f"{e['date']}: {e['kind'][:60]}")
        else:
            factors[parse_iso_day(e["date"]).isoformat()] = ratio
    n = store.upsert_split_factors(symbol, factors)
    return n, unresolved


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
        ratio = None
        for k in CA_OPTIONAL_KEYS:
            if r.get(k) not in (None, ""):
                ratio = str(r[k])
                break
        out.append({"date": got["date"], "kind": got["type"],
                    "ratio": ratio, "symbol": symbol.upper()})
        # faceVal, when the row carries it, is kept in ITS OWN field (never
        # inside `kind`: dividend rows also carry faceVal, and polluting kind
        # would make the split classifier fire on dividends). What the row does
        # NOT give is the face value BEFORE the action, so nothing downstream
        # derives a ratio from it — the raw number is kept for a human to read
        # against the exchange record.
        if r.get("faceVal") not in (None, ""):
            try:
                out[-1]["faceVal"] = float(r["faceVal"])
            except (TypeError, ValueError):
                out[-1]["faceVal"] = None
    return out


def parse_iso_day(d: str):
    """"26-Aug-2025" or ISO -> datetime.date. Raises on anything else — a
    corporate-action date that can't be read is an error, never a skipped row."""
    from datetime import date as _date, datetime

    s = str(d).strip()
    try:
        return _date.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable corporate-action date: {s!r}")


def is_split_kind(kind: str) -> bool:
    """Split/bonus classifier: only words seen in NSE pattern rows. Ambiguous
    purposes are NOT splits — they're never silently used as factors."""
    return bool(re.search(r"split|bonus|sub\s*div|face\s*val", str(kind), re.I))


def _ratio_from_num_text(kind: str) -> float | None:
    """The only place the ratio comes from: a NUMBERED statement inside the
    subject — 'Bonus 1:1', '1:2 split', 'Re-issue... from Rs 10 to Rs 2'.
    Bonus text is arithmetic (a:b -> a/(a+b)); plain ratios/face values are read
    as the shrink because NSE notation direction varies and the price effect of
    a split is always a fall. A '1:1' with no bonus/split word, or text with no
    numbers, returns None — the caller then names the event as UNRESOLVED,
    and health's split-gap keeps the symbol red until a human reads it. That is
    the correct cost of not guessing: HDFCBANK's 2025 bonus sits in exactly
    this state (the probe's own paste shows the subject had no ratio), so its
    factor is NOT stored and the cliff stays flagged."""
    s = str(kind).lower()
    is_bonus = "bonus" in s
    m = re.search(r"(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)", s)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if a <= 0 or b <= 0:
            return None
        if is_bonus:
            return round(a / (a + b), 6)
        return round(min(a, b) / max(a, b), 6) if a != b else None
    if is_bonus and re.search(r"bonus\s+\d", s):  # "Bonus 1" = one new per held
        return 0.5
    m = re.search(r"(?:rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(?:/-)?\s*to\s*(?:rs\.?|inr)?\s*(\d+(?:\.\d+)?)", s)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if a <= 0 or b <= 0 or a == b:
            return None
        return round(min(a, b) / max(a, b), 6)
    return None


def _split_ratio(subject: str) -> float | None:
    """Price multiplier a split/bonus imposes, or None for 'not a split' /
    'no usable numbers' — one convention, documented in _ratio_from_num_text."""
    s = str(subject).lower()
    if not re.search(r"bonus|split|sub\s*div|sub-div|face\s*val", s):
        return None
    return _ratio_from_num_text(s)


def record_parsed(store, symbol: str, events: list[dict]) -> str:
    """Store events + split factors, one-line result (also used by --from-raw)."""
    n_ev = upsert_corporate_actions(store, symbol, events)
    n_sf, unresolved = build_split_factors(store, symbol, events)
    note = f"{len(events)} events parsed; {n_ev} stored, {n_sf} split factors"
    if unresolved:
        note += f"; UNRESOLVED {len(unresolved)}: " + " | ".join(unresolved[:3])
    return note


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
    non-empty payload, report every call's shape. The 'equities' option_type
    form is tried first for actions() (laptop's round-5 signature claim —
    unverified by paste, so the old forms remain as fallback and a TypeError
    on the guessed form is REPORTED, not swallowed)."""
    if client is None:
        from indtrader.data.nseapi_provider import NseIndiaApiProvider
        client = NseIndiaApiProvider().client()
    cands = [m for m in dir(client)
             if "corporate" in m.lower() or "ca_" in m.lower() or "action" in m.lower()]
    if not cands:
        return {"ok": False, "error": f"no corporate-actions method in {type(client).__name__}"}
    sym = symbol.upper()
    probed, stored_any = [], False
    for m in cands:
        meth = getattr(client, m)
        forms = [((sym,), {"option_type": "equities"}),  # the signature claim, tested first
                 (("equities", sym), {}), (("equities",), {"symbol": sym}),
                 ((sym,), {}), ((sym, "cm"), {}), ((sym, "F&O"), {}), ((), {})]
        for args, kwargs in forms:
            label = f"{m}({', '.join(map(repr, args))}" + \
                    (f", {kwargs}" if kwargs else "") + ")"
            try:
                data = meth(*args, **kwargs)
            except TypeError as e:
                probed.append(f"{label}: signature-miss {str(e)[:60]}")
                continue  # wrong arg form for this signature — try the next form
            except Exception as e:
                probed.append(f"{label}: {type(e).__name__}: {str(e)[:90]}")
                break  # this method's endpoint itself failed — don't hammer it
            note = payload_shape(data)
            try:
                events = parse_events(sym, data)
                note += " -> " + record_parsed(store, sym, events)
            except (KeyError, ValueError):
                pass  # unconfirmed shape: the shape report IS the deliverable
            probed.append(f"{label}: {note}")
            if not _emptyish(data):
                store_raw(store, sym, label, data)
                stored_any = True
                break  # rows found for this method — other arg forms rarely add more
    if not stored_any:
        return {"ok": False, "probed": probed,
                "error": "every corporate-actions call returned empty/err — "
                         "paste this inventory, it decides the next move"}
    return {"ok": True, "stored_any": True, "probed": probed,
            "next": "coractions " + sym + " --from-raw  (replay parser over stored payloads)"}
