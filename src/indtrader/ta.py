"""Indicators done right — the stock-analyzer lesson: Cutler RSI diverges from every
charting platform and its flat-price path returned 100. Here RSI is Wilder's smoothing,
flat market returns 50 (neutral), and everything is tested against known values.

All functions take an ascending pandas Series/DataFrame (daily bars unless stated).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).mean()


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI — the exact original recursion (initial mean, then smoothing),
    NOT an EWM approximation, so values match Wilder's tables and charting platforms.
    Flat prices -> 50 (never the fake-100 bug we caught in stock-analyzer)."""
    delta = s.diff().to_numpy(dtype="float64")
    n = len(s)
    out = np.full(n, np.nan)
    if n <= period:
        return pd.Series(out, index=s.index)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)
    avg_gain = float(np.mean(gains[1 : period + 1]))
    avg_loss = float(np.mean(losses[1 : period + 1]))

    def _rsi(ag: float, al: float) -> float:
        if ag == 0 and al == 0:
            return 50.0
        if al == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + ag / al)

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, n):
        if np.isnan(delta[i]):
            out[i] = np.nan
            continue
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i] = _rsi(avg_gain, avg_loss)
    return pd.Series(out, index=s.index)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR (RMA smoothing)."""
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(s, fast) - ema(s, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(s: pd.Series, period: int = 20, mult: float = 2.0) -> pd.DataFrame:
    mid = sma(s, period)
    std = s.rolling(period, min_periods=period).std(ddof=0)
    return pd.DataFrame({"mid": mid, "upper": mid + mult * std, "lower": mid - mult * std})


def donchian(df: pd.DataFrame, period: int = 55) -> pd.DataFrame:
    upper = df["high"].rolling(period, min_periods=period).max()
    lower = df["low"].rolling(period, min_periods=period).min()
    return pd.DataFrame({"upper": upper, "lower": lower, "mid": (upper + lower) / 2})


def annualized_vol(s: pd.Series, window: int = 20) -> pd.Series:
    """20-day rolling vol of simple returns, annualized (sqrt 252)."""
    return s.pct_change().rolling(window, min_periods=window).std(ddof=0) * np.sqrt(252)


def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> pd.DataFrame:
    """Iterative ATR-band supertrend with the standard ratcheting bands."""
    _atr = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    upper_basic = hl2 + mult * _atr
    lower_basic = hl2 - mult * _atr

    n = len(df)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    trend = np.zeros(n, dtype=int)  # 1 up, -1 down
    close = df["close"].to_numpy()
    ub = upper_basic.to_numpy()
    lb = lower_basic.to_numpy()

    started = False
    for i in range(n):
        if np.isnan(ub[i]) or np.isnan(lb[i]):
            continue
        if not started:
            final_upper[i] = ub[i]
            final_lower[i] = lb[i]
            trend[i] = 1 if close[i] > ub[i] else -1
            started = True
            continue
        prev_fu, prev_fl, prev_t = final_upper[i - 1], final_lower[i - 1], trend[i - 1]
        final_upper[i] = ub[i] if (ub[i] < prev_fu or close[i - 1] > prev_fu) else prev_fu
        final_lower[i] = lb[i] if (lb[i] > prev_fl or close[i - 1] < prev_fl) else prev_fl
        if prev_t == 1:
            trend[i] = -1 if close[i] < final_lower[i] else 1
        else:
            trend[i] = 1 if close[i] > final_upper[i] else -1
    return pd.DataFrame({"st": np.where(trend == 1, final_lower, final_upper), "trend": trend}, index=df.index)


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Daily -> weekly OHLCV, week ending Friday; complete weeks only.

    A trailing partial week is dropped so today's unfinished bar never pollutes
    weekly signals (the RRG loader got this part right).
    """
    if df.empty:
        return df
    out = (
        df.resample("W-FRI")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["close"])
    )
    last_daily = pd.Timestamp(df.index[-1]).normalize()
    if not out.empty:
        wk = out.index[-1]
        # Friday-labelled bucket holding this week's data: partial if last daily < bucket Friday
        # and last daily is within 4 business days before it — simply: drop if last trade date
        # is strictly before the bucket's Friday and today is not a Friday session
        if last_daily < wk and (wk - last_daily).days < 7:
            out = out.iloc[:-1]
    return out
