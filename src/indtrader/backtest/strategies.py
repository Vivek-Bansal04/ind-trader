"""Swing/positional strategies on DAILY bars — nothing here can look at sub-daily data.

Encoding what the literature repeatedly found works on daily/weekly horizons:
- Trend gate close > SMA200 (Faber 2007; Smith & N. 2013 found ~200d MA beats short/complex rules)
- Time-series momentum with sign(6-12m return) (Moskowitz Ooi Pedersen 2012)
- Dual momentum: absolute + relative (Antonacci 2012)
- Donchian breakout (Turtle-style) with slow filter (Whitmore/Wagner style)
- RSI mean-reversion only INSIDE an uptrend (low-RSI-in-downtrend is a falling knife)
Signal convention: 1 = long, 0 = flat. Long-only, delivery.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indtrader import ta


class Strategy:
    name = "base"
    description = ""

    def signals(self, df: pd.DataFrame) -> pd.Series:
        """Return Series[int 0|1] aligned to df.index. Signal on bar i executes at open i+1 —
        the engine enforces that; never shift yourself."""
        raise NotImplementedError


class TrendSMA(Strategy):
    name = "trend_sma200"
    description = "Long while close > SMA(200) — the Faber regime filter."

    def __init__(self, period: int = 200):
        self.period = period

    def signals(self, df: pd.DataFrame) -> pd.Series:
        return (df["close"] > ta.sma(df["close"], self.period)).astype("int8")


class DualMA(Strategy):
    name = "dual_ma"
    description = "Fast EMA over slow EMA, with a 200-day regime gate."

    def __init__(self, fast: int = 20, slow: int = 100, gate: int = 200):
        self.fast, self.slow, self.gate = fast, slow, gate

    def signals(self, df: pd.DataFrame) -> pd.Series:
        f, s, g = ta.ema(df["close"], self.fast), ta.ema(df["close"], self.slow), ta.sma(df["close"], self.gate)
        return ((f > s) & (df["close"] > g)).astype("int8")


class DonchianBreakout(Strategy):
    name = "donchian"
    description = "Buy 55-day-high breakout, exit 20-day-low; only above the 200-day gate."

    def __init__(self, entry: int = 55, exit_: int = 20, gate: int = 200):
        self.entry, self.exit, self.gate = entry, exit_, gate

    def signals(self, df: pd.DataFrame) -> pd.Series:
        dc = ta.donchian(df, self.entry)
        gate_up = df["close"] > ta.sma(df["close"], self.gate)
        exit_line = df["low"].rolling(self.exit, min_periods=self.exit).min()
        enter = (df["close"] > dc["upper"].shift(1)) & gate_up  # break above prior channel
        pos = pd.Series(np.nan, index=df.index, dtype="float64")
        pos[enter] = 1
        pos[df["close"] < exit_line] = 0
        out = pos.ffill().fillna(0).astype("int8")
        return out


class TSMomentum(Strategy):
    name = "ts_mom"
    description = "Time-series momentum: long while 6-month return > 0 and above SMA200 (Moskowitz-style)."

    def __init__(self, lookback: int = 126, gate: int = 200):
        self.lookback, self.gate = lookback, gate

    def signals(self, df: pd.DataFrame) -> pd.Series:
        mom = df["close"].pct_change(self.lookback)
        return ((mom > 0) & (df["close"] > ta.sma(df["close"], self.gate))).astype("int8")


class RSIRegression(Strategy):
    name = "rsi_reg"
    description = "Buy oversold RSI(2) dips but ONLY while close > SMA200 (never in downtrends)."

    def __init__(self, rsi_period: int = 2, buy_below: float = 10.0, exit_above: float = 60.0, gate: int = 200):
        self.r, self.buy_below, self.exit_above, self.gate = rsi_period, buy_below, exit_above, gate

    def signals(self, df: pd.DataFrame) -> pd.Series:
        r = ta.rsi(df["close"], self.r)
        gate_up = df["close"] > ta.sma(df["close"], self.gate)
        pos = pd.Series(np.nan, index=df.index, dtype="float64")
        pos[(r < self.buy_below) & gate_up] = 1
        pos[r > self.exit_above] = 0
        return pos.ffill().fillna(0).astype("int8")


class DualMomentum(Strategy):
    """Antonacci dual momentum for a single asset (relative leg is the scanner's job)."""

    name = "dual_mom"
    description = "Long while 12m return > T-bill proxy (6%) and > 0; else cash."

    def __init__(self, lookback: int = 252, abs_thresh: float = 0.06):
        self.lookback, self.abs_thresh = lookback, abs_thresh

    def signals(self, df: pd.DataFrame) -> pd.Series:
        return (df["close"].pct_change(self.lookback) > self.abs_thresh).astype("int8")


REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls for cls in [TrendSMA, DualMA, DonchianBreakout, TSMomentum, RSIRegression, DualMomentum]
}
