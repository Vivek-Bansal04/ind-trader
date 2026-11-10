"""Position sizing: fixed-fractional risk with an ATR-defined stop, correlation penalty,
and hard caps. (Half-Kelly-style thinking from the engine we audited, simplified and
testable. Volatility-targeted sizing per Moskowitz et al. 2012 spirit.)"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from indtrader.backtest.costs import DeliveryCosts
from indtrader.backtest.costs import buy_qty_for


@dataclass(frozen=True)
class SizingResult:
    qty: int
    risk_amount: float
    per_share_risk: float
    reason: str


def size_position(
    equity: float,
    price: float,
    atr_value: float,
    cash: float | None = None,
    risk_per_trade: float = 0.01,     # 1% of equity at risk to the stop
    stop_atr_mult: float = 3.0,
    max_alloc_frac: float = 0.10,     # never >10% of equity in one name
    corr_penalty: float = 0.0,        # 0..1, e.g. max |corr| with existing holdings
    costs: DeliveryCosts | None = None,
) -> SizingResult:
    """qty = min(risk-basis, allocation-basis, correlation-adjusted, cash-affordable)."""
    costs = costs or DeliveryCosts()
    if price <= 0 or atr_value <= 0 or equity <= 0:
        return SizingResult(0, 0.0, 0.0, "invalid inputs")
    per_share_risk = stop_atr_mult * atr_value
    if per_share_risk <= 0 or per_share_risk >= price:
        return SizingResult(0, 0.0, per_share_risk, "stop wider than price")

    risk_amount = risk_per_trade * equity * (1.0 - corr_penalty)
    qty_risk = risk_amount / per_share_risk
    qty_alloc = (max_alloc_frac * equity) / price
    qty = int(min(qty_risk, qty_alloc))
    reason = "risk" if qty_risk <= qty_alloc else "alloc"
    if corr_penalty > 0:
        reason += f" (corr-penalty {corr_penalty:.0%})"
    if cash is not None:
        qty = min(qty, buy_qty_for(cash, price, costs))
    return SizingResult(max(qty, 0), risk_amount, per_share_risk, reason)


def correlation_penalty(closes: pd.Series, existing: list[pd.Series], threshold: float = 0.7) -> float:
    """Max Pearson corr of this candidate's daily returns vs each holding (>= threshold);
    returns a 0..0.5 penalty to shrink size, 0 if uncorrelated or no holdings."""
    if not existing:
        return 0.0
    r = closes.pct_change().dropna()
    worst = 0.0
    for other in existing:
        r2 = other.pct_change().dropna()
        joined = pd.concat([r, r2], axis=1).dropna()
        if len(joined) < 60:
            continue
        corr = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
        if not np.isnan(corr) and abs(corr) > worst:
            worst = abs(corr)
    if worst < threshold:
        return 0.0
    return min(0.5, (worst - threshold) / (1.0 - threshold) * 0.5)
