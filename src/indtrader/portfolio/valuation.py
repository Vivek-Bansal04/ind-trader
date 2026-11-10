"""Money-weighted return of the ACTUAL book: fills ledger + live marks -> XIRR.

The XIRR engine (portfolio/xirr.py) was built and golden-tested months ago; this is
its wiring. If any held symbol has no recent close, the answer is MISSING MARKS,
not a guessed value — a wrong denominator poisons the whole rate.
"""
from __future__ import annotations

from datetime import date

from indtrader.portfolio.xirr import xirr


def book_flows(fills) -> list[tuple[date, float]]:
    """BUY = outflow incl. costs; SELL = inflow net of costs (dt, amount)."""
    out = []
    for f in fills:
        gross = f.qty * f.price
        amt = -(gross + f.costs) if f.side.upper() == "BUY" else (gross - f.costs)
        out.append((f.dt, amt))
    return out


def held_quantities(fills) -> dict[str, int]:
    q: dict[str, int] = {}
    for f in fills:
        q[f.symbol] = q.get(f.symbol, 0) + (f.qty if f.side.upper() == "BUY" else -f.qty)
    return {s: v for s, v in q.items() if v != 0}


def resolve_marks(store, symbols, equity_col: str = "close") -> dict:
    """symbol -> last value, or None when unknown (never a guess). MF: symbols price
    from stored NAVs; everything else from its candles — wrong source is worse than
    no source."""
    from indtrader.portfolio.mf import is_mf, mf_mark

    out = {}
    for sym in symbols:
        if is_mf(sym):
            out[sym] = mf_mark(store, sym)
            continue
        df = store.load(sym)
        out[sym] = float(df[equity_col].iloc[-1]) if not df.empty else None
    return out


def book_xirr(fills, last_close: dict[str, float | None], asof: date | None = None) -> dict:
    asof = asof or date.today()
    flows = book_flows(fills)
    if len(flows) < 2:
        return {"ok": False, "reason": "fewer than 2 fills — XIRR needs a cashflow history"}
    held = held_quantities(fills)
    missing = [s for s in held if last_close.get(s) is None]
    if missing:
        return {"ok": False, "missing_marks": sorted(missing),
                "reason": "no recent close for held symbols — refusing to guess their value "
                          "(fetch their bars, then rerun)"}
    value = sum(q * float(last_close[s]) for s, q in held.items())
    series = sorted(flows + ([(asof, value)] if value > 0 else []))
    r = xirr(series)
    invested = -sum(a for _, a in flows if a < 0)
    realized = sum(a for _, a in flows if a > 0)
    return {"ok": True, "asof": str(asof), "xirr": None if r is None else round(r, 4),
            "invested": round(invested, 2), "realized_inflow": round(realized, 2),
            "open_value": round(value, 2),
            "note": "xirr None = no root in bracket (e.g. all-positive flows); reported, not hidden"}
