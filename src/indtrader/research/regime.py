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
    ftd: dict | None = None  # follow_through() info line; never affects exposure_scale


def classify(breadth: float | None, index_above_200dma: bool | None,
             index_sma200: float | None = None, universe_n: int | None = None,
             ftd: dict | None = None) -> Regime:
    if breadth is None or index_above_200dma is None:
        return Regime("NEUTRAL", 0.6, breadth, index_above_200dma, index_sma200, universe_n, ftd)
    if breadth >= 0.60 and index_above_200dma:
        return Regime("ON", 1.0, breadth, index_above_200dma, index_sma200, universe_n, ftd)
    if breadth <= 0.35 or not index_above_200dma:
        return Regime("OFF", 0.30, breadth, index_above_200dma, index_sma200, universe_n, ftd)
    return Regime("NEUTRAL", 0.60, breadth, index_above_200dma, index_sma200, universe_n, ftd)


def regime_from_data(close_map: dict[str, pd.Series], index_closes: pd.Series,
                     index_volume: pd.Series | None = None) -> Regime:
    breadth = fraction_above_200dma(close_map)
    n = sum(1 for closes in close_map.values()
            if sma(closes, 200).notna().sum() and closes.notna().sum())
    s = sma(index_closes, 200).dropna()
    idx_up = bool(index_closes.dropna().iloc[-1] > s.iloc[-1]) if len(s) else None
    return classify(breadth, idx_up, index_sma200=(float(s.iloc[-1]) if len(s) else None),
                    universe_n=(n or None), ftd=follow_through(index_closes, index_volume))


def flip_note(regime: Regime | None, ftd: dict | None = None) -> str:
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
    if ftd is not None:
        th = f"{ftd['threshold_pct']:.2f}%"
        if ftd["ftd_date"] is not None and ftd["days_since"] <= FTD_WATCH_SESSIONS:
            parts.append(f"FTD {ftd['days_since']} sessions ago (day {ftd['ftd_day_no']} "
                         f"by {th}+, on volume)")
        elif ftd["state"] == "rally_attempt":
            parts.append(f"rally attempt day {ftd['attempt_day_no']}, FTD needs a "
                         f"{th}+ day from day {FTD_MIN_DAY}")
        elif ftd["state"] == "correction":
            parts.append(f"correction, awaiting FTD (needs {th}+ from day {FTD_MIN_DAY})")
    return "; ".join(parts)


# --- follow-through (FTD): informational market-internals, never touches exposure ---
# State machine from the claude-skills market-regime skill (MIT), simplified to closes
# (closes double as the undercut proxy) and one key fix their own audit flagged: the
# advance threshold is σ-scaled — clamp(1.5 × σ(lookback), 1%, 2%) — because a fixed
# 1.5% almost never fires on NIFTY.
FTD_CORRECTION_PCT = 0.05
FTD_MIN_DAY = 4
FTD_WATCH_SESSIONS = 25
FTD_LOOKBACK = 300


def follow_through(closes: pd.Series, volume: pd.Series | None = None,
                   lookback: int = FTD_LOOKBACK) -> dict | None:
    """Scan the last `lookback` index sessions once. Returns None on < 60 bars —
    absent data stays absent, it never defaults to a friendly state."""
    c = closes.dropna().tail(lookback)
    if len(c) < 60:
        return None
    sigma = float(c.pct_change().std())
    threshold = min(0.02, max(0.01, 1.5 * sigma))
    cl = c.to_numpy(dtype=float)
    v = volume.dropna().reindex(c.index).to_numpy(dtype=float) if volume is not None else None
    has_vol = v is not None and not bool(pd.isna(v).any())
    state, peak, low_i, day1_i, ftd_i, ftd_day = "uptrend", 0, 0, None, None, None
    for i in range(1, len(cl)):
        chg = cl[i] / cl[i - 1] - 1.0
        if state == "uptrend":
            peak = i if cl[i] > cl[peak] else peak
            if cl[i] <= cl[peak] * (1 - FTD_CORRECTION_PCT):
                state, low_i = "correction", i
            continue
        if cl[i] > cl[peak]:  # V-shaped recovery / continuation without needing a new FTD
            state, peak = "uptrend", i
            continue
        if state == "follow_through":
            if cl[i] < cl[low_i]:  # closed below the rally low — the FTD failed
                state, low_i = "correction", i
            elif i - ftd_i > FTD_WATCH_SESSIONS:
                state = "uptrend"
            continue
        if state == "correction":
            low_i = i if cl[i] < cl[low_i] else low_i
            if chg > 0:
                state, day1_i = "rally_attempt", i
            continue
        # rally_attempt
        if cl[i] < cl[low_i]:  # undercut kills the attempt; new correction, day 1 resets
            state, low_i = "correction", i
            continue
        day = i - day1_i + 1
        vol_up = has_vol and not pd.isna(v[i]) and not pd.isna(v[i - 1]) and v[i] > v[i - 1]
        if day >= FTD_MIN_DAY and chg >= threshold and vol_up:
            state, ftd_i, ftd_day, peak = "follow_through", i, day, i
    dates = c.index
    last = len(cl) - 1
    return {
        "state": state,
        "ftd_date": str(dates[ftd_i].date()) if ftd_i is not None else None,
        "days_since": (last - ftd_i) if ftd_i is not None else None,
        "ftd_day_no": ftd_day,
        "attempt_day_no": (last - day1_i + 1) if (state == "rally_attempt" and day1_i is not None) else None,
        "threshold_pct": round(100 * threshold, 2),
        "volume_checked": has_vol,
    }
