"""Regime: one traffic light that scales gross exposure.
Components: breadth (% universe above 200-DMA — Faber's rule applied market-wide) and
trend of the benchmark index. VIX/FII-DII join in Phase 3 when feeds are wired.

Literature basis: trend regime filters materially cut drawdowns in TS-momentum books
(Moskowitz 2012; Barroso & Santa-Clara 2015 momentum-crash fix).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from indtrader.ta import sma


def fraction_above_200dma(close_map: dict[str, pd.Series]) -> float | None:
    vals = []
    for closes in close_map.values():
        s = sma(closes, 200)
        if s.notna().sum() and closes.notna().sum():
            vals.append(1.0 if closes.dropna().iloc[-1] > s.dropna().iloc[-1] else 0.0)
    if not vals:
        return None
    return sum(vals) / len(vals)


@dataclass(frozen=True)
class Regime:
    label: str  # ON | NEUTRAL | OFF
    exposure_scale: float  # multiply gross exposure by this
    breadth: float | None
    index_trend_up: bool | None


def classify(breadth: float | None, index_above_200dma: bool | None) -> Regime:
    if breadth is None or index_above_200dma is None:
        return Regime("NEUTRAL", 0.6, breadth, index_above_200dma)
    if breadth >= 0.60 and index_above_200dma:
        return Regime("ON", 1.0, breadth, index_above_200dma)
    if breadth <= 0.35 or not index_above_200dma:
        return Regime("OFF", 0.30, breadth, index_above_200dma)
    return Regime("NEUTRAL", 0.60, breadth, index_above_200dma)


def regime_from_data(close_map: dict[str, pd.Series], index_closes: pd.Series) -> Regime:
    breadth = fraction_above_200dma(close_map)
    s = sma(index_closes, 200).dropna()
    idx_up = bool(index_closes.dropna().iloc[-1] > s.iloc[-1]) if len(s) else None
    return classify(breadth, idx_up)
