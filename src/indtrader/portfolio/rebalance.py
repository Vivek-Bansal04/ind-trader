"""Monthly rebalance + tax lens (plan Phase 6): current book vs target weights,
with the tax cost of each proposed trade — so a rebalance decision sees its own bill.

Honesty rules carried over from the rest of the repo:
- marks or closes unknown -> the symbol is reported UNKNOWN, never priced from a guess;
- the FIFO holding-period engine covers EQUITY and (broadly) equity-oriented funds;
  debt-fund units get a slab-rate flag, not a fake 12.5%;
- proposals are PROPOSALS: human-confirmed execution is a standing rule, so nothing
  here can act. It answers "what and roughly what it costs", the human decides.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Trade:
    symbol: str
    action: str          # BUY | SELL
    qty: float
    price: float | None
    est_value: float     # qty * price (0.0 when price unknown)
    note: str = ""


def rebalance_plan(held_qty: dict[str, float], marks: dict[str, float | None],
                   targets: dict[str, float], closes: dict[str, list] | None = None,
                   tol: float = 0.02, max_new: float = 0.25) -> list[Trade]:
    """Trades to move the marked book toward targets. tol = no-trade band (fraction of
    TOTAL value, so 0.02 = churn stays under 2%/name). closes: per-symbol session
    closes (oldest first) for FIFO holding-period estimates."""
    closes = closes or {}
    values = {}
    for sym, q in held_qty.items():
        values[sym] = q * marks[sym] if marks.get(sym) is not None else None
    known_total = sum(v for v in values.values() if v is not None)
    cash_share = max(0.0, 1.0 - sum(targets.values()))
    trades: list[Trade] = []

    # sells and buys in one pass; sells raise the cash the buys conceptually need
    for sym, tgt in sorted(targets.items()):
        px = marks.get(sym)
        if sym not in held_qty:
            # an ENTRY: a target without a price can't be sized — unknown != zero.
            if tgt > 0:
                if px is None:
                    trades.append(Trade(sym, "BUY", 0.0, None, 0.0,
                                        "new name, no mark — fetch NAV/bars to size (never guessed)"
                                        + (f"; target {tgt:.0%} > max-new {max_new:.0%}, staged entry needed"
                                           if tgt > max_new else "")))
                else:
                    need = tgt * known_total
                    trades.append(Trade(sym, "BUY", round(need / px, 4), px, round(need, 2), "new entry"))
            continue
        cur_val = values.get(sym)
        if cur_val is None:
            if tgt > 0:
                trades.append(Trade(sym, "HOLD", 0.0, None, 0.0,
                                    "held, mark unknown — fetch NAV/bars before rebalancing"))
            continue
        cur_share = cur_val / known_total if known_total else 0.0
        drift = cur_share - tgt
        if drift < -tol:
            need = -drift * known_total
            if px is None:
                trades.append(Trade(sym, "BUY", 0.0, None, round(need, 2),
                                    "underweight but mark unknown — sized 0"))
                continue
            trades.append(Trade(sym, "BUY", round(need / px, 4), px, round(need, 2),
                                _period_note(sym, closes)))
        elif drift > tol:
            excess = min(drift * known_total, cur_val)
            if px is None:
                trades.append(Trade(sym, "SELL", 0.0, None, round(excess, 2),
                                    "overweight but mark unknown — sized 0"))
                continue
            note = _period_note(sym, closes)
            trades.append(Trade(sym, "SELL", round(excess / px, 4), px, round(excess, 2),
                                (note + "; " if note else "") + "tax on realized lots: run tax --setoff"))
    if cash_share > tol:
        trades.append(Trade("CASH", "HOLD", 0.0, None, round(cash_share * known_total, 2),
                            "target weights leave this residual in cash"))
    return trades


def _period_note(sym: str, closes: dict[str, list]) -> str:
    """Holding-period tax hint using the FIFO engine's 12-month boundary."""
    from datetime import date

    from indtrader.portfolio.mf import is_mf
    from indtrader.portfolio.tax import months_between

    if is_mf(sym):
        return "MF: equity-oriented -> same STCG/LTCG rules; DEBT funds -> slab-rate rules, engine does not model"
    hist = closes.get(sym) or []
    if len(hist) < 2:
        return "holding period unknown — run tax --setoff for the real bill"
    return ""  # exact lots come from the tax engine; this hint only flags the unknowns
