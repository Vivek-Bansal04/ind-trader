"""Regime: one traffic light that scales gross exposure.
Components: breadth (% universe above 200-DMA — Faber's rule applied market-wide) and
trend of the benchmark index. VIX/FII-DII join in Phase 3 when feeds are wired.

Literature basis: trend regime filters materially cut drawdowns in TS-momentum books
(Moskowitz 2012; Barroso & Santa-Clara 2015 momentum-crash fix).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor

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
    index_sma200: float | None = None  # benchmark's own gate level, for flip_note
    universe_n: int | None = None  # how many names actually counted toward breadth


def classify(breadth: float | None, index_above_200dma: bool | None,
             index_sma200: float | None = None, universe_n: int | None = None) -> Regime:
    if breadth is None or index_above_200dma is None:
        return Regime("NEUTRAL", 0.6, breadth, index_above_200dma, index_sma200, universe_n)
    if breadth >= 0.60 and index_above_200dma:
        return Regime("ON", 1.0, breadth, index_above_200dma, index_sma200, universe_n)
    if breadth <= 0.35 or not index_above_200dma:
        return Regime("OFF", 0.30, breadth, index_above_200dma, index_sma200, universe_n)
    return Regime("NEUTRAL", 0.60, breadth, index_above_200dma, index_sma200, universe_n)


def regime_from_data(close_map: dict[str, pd.Series], index_closes: pd.Series) -> Regime:
    breadth = fraction_above_200dma(close_map)
    n = sum(1 for closes in close_map.values()
            if sma(closes, 200).notna().sum() and closes.notna().sum())
    s = sma(index_closes, 200).dropna()
    idx_up = bool(index_closes.dropna().iloc[-1] > s.iloc[-1]) if len(s) else None
    return classify(breadth, idx_up, index_sma200=(float(s.iloc[-1]) if len(s) else None),
                    universe_n=(n or None))


def flip_note(regime: Regime | None) -> str:
    """Plain sentence naming the exact levels that would change the label — the anti-
    vagueness rule from the claude-skills audit. Shows current breadth next to the
    threshold so the human can see how far away a flip actually is. 'unknown' is
    printed as unknown, never rounded off to a comfortable number."""
    if regime is None:
        return ""
    pct = lambda b: ("unknown" if b is None else f"{100 * b:.0f}%")  # noqa: E731
    parts = [f"Regime {regime.label} — breadth {pct(regime.breadth)}"]
    n = regime.universe_n or 0
    above_now = int(round(regime.breadth * n)) if (regime.breadth is not None and n) else None
    if regime.label == "ON":
        down = ["index close below its 200-DMA"]
        if above_now is not None:
            k = above_now - floor(0.35 * n)
            down.append(f"breadth under 35% ({k} of {n} names must drop below their 200-DMA)")
        parts.append("turns OFF if " + " or ".join(down))
    elif regime.label == "OFF":
        up = ["index back above its 200-DMA"]
        if above_now is not None:
            k = max(1, ceil(0.60 * n) - above_now)
            up.append(f"breadth to 60% ({k} more of {n} names above their 200-DMA)")
        if regime.index_trend_up is not True and regime.index_sma200:
            up[0] += f" ({regime.index_sma200:,.0f})"
        parts.append("turns ON only if " + " and ".join(up))
    else:  # NEUTRAL — say exactly what is missing
        missing = []
        if regime.breadth is None:
            missing.append("breadth unknown (no universe data)")
        elif regime.breadth < 0.60:
            missing.append(f"breadth {pct(regime.breadth)} below 60% gate")
        if regime.index_trend_up is not True:
            lvl = f" ({regime.index_sma200:,.0f})" if regime.index_sma200 else ""
            missing.append("index not above its 200-DMA" + lvl)
        parts.append("ON needs " + " and ".join(missing) if missing
                     else "ON needs breadth >=60% and index above 200-DMA")
    return "; ".join(parts)
