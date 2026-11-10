"""Wilder RSI, ATR, weekly resampling — the indicators stock-analyzer got subtly wrong."""
import numpy as np
import pandas as pd
import pytest

from indtrader.ta import atr, ema, resample_weekly, rsi, sma, supertrend, true_range

# The classic Wilder example series (RSI charting textbook): final RSI14 ~ 70.46
WILDER = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
          45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64]


def test_rsi_wilder_golden():
    # Wilder's textbook series: first RSI14 = 70.464 at index 14 (hand-verified:
    # avg gain 3.34/14 vs avg loss 1.40/14 -> RS 2.386), then exact recursion.
    s = pd.Series(WILDER)
    out = rsi(s, 14)
    assert abs(out.iloc[14] - 70.464) < 0.01
    assert abs(out.iloc[-1] - 57.915) < 0.05


def test_rsi_flat_is_neutral_not_100():
    # stock-analyzer's flat-price bug returned 100 => fake "overbought"
    s = pd.Series([100.0] * 40)
    assert rsi(s, 14).iloc[-1] == pytest.approx(50.0)


def test_rsi_monotonic_up_is_high():
    s = pd.Series(np.linspace(100, 200, 40))
    assert rsi(s, 14).iloc[-1] > 95


def test_atr_constant_range():
    idx = pd.date_range("2024-01-01", periods=40, freq="D")
    df = pd.DataFrame({"open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0, "volume": 1e6}, index=idx)
    tr = true_range(df)
    assert tr.iloc[1] == pytest.approx(4.0)
    assert atr(df, 14).iloc[-1] == pytest.approx(4.0, rel=0.05)


def test_ema_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5, 6], dtype=float)
    assert sma(s, 3).iloc[-1] == pytest.approx(5.0)
    # ewm span=3 (alpha=0.5, adjust=False): 1,1.5,2.25,3.125,4.0625,5.03125
    assert ema(s, 3).iloc[-1] == pytest.approx(5.03125, rel=1e-9)


def test_resample_weekly_drops_partial():
    # 3 full weeks + 2 days of a partial week: last row of partial must be dropped
    idx = pd.bdate_range("2024-01-01", "2024-01-24")  # Mon Jan 1 .. Wed Jan 24
    df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": np.arange(len(idx), dtype=float) + 1,
                       "volume": 100.0}, index=idx)
    w = resample_weekly(df)
    assert len(w) == 3  # Jan 5, 12, 19 — the Jan 24 partial week is excluded
    assert w["close"].iloc[-1] == df.loc["2024-01-19", "close"]


def test_supertrend_shape_and_bounds():
    n = 260
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2023-01-02", periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e6},
                      index=idx)
    st = supertrend(df, 10, 3.0)
    tail = st.dropna().tail(30)
    close_tail = df["close"].loc[tail.index]
    assert set(tail["trend"].unique()) <= {1, -1}
    ok = tail["trend"] == 1
    dn = tail["trend"] == -1
    # up-trend: trailing support (st) sits at/below close; down-trend: resistance at/above close
    assert (tail.loc[ok, "st"] <= close_tail[ok] + 1e-6).all()
    assert (tail.loc[dn, "st"] >= close_tail[dn] - 1e-6).all()
