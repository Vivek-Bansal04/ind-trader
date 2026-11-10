import numpy as np
import pandas as pd
import pytest

from indtrader.backtest.costs import DeliveryCosts
from indtrader.backtest.engine import Backtester
from indtrader.backtest.strategies import DonchianBreakout, TrendSMA


def rising_df(n=400, slope=0.001, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(slope, 0.012, n)))
    return pd.DataFrame({"open": close * 0.999, "high": close * 1.008, "low": close * 0.992,
                         "close": close, "volume": 1e6}, index=idx)


def flat_then_rising_df(n=400, seed=11):
    """Flat base then 0.3%/day drift WITH noisy highs (real markets break 20d highs
    even in gentle uptrends; a mechanical ±1% band would make breakouts impossible)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    close = np.r_[np.full(150, 100.0), 100 * np.exp(np.cumsum(np.full(n - 150, 0.003)))]
    close = close * np.exp(rng.normal(0, 0.004, n))
    high = close * (1 + np.abs(rng.normal(0.004, 0.002, n)))
    low = close * (1 - np.abs(rng.normal(0.004, 0.002, n)))
    open_ = (high + low) / 2
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1e6}, index=idx)


def test_costs_reduce_final_equity():
    df = rising_df()
    strat = DonchianBreakout(entry=20, exit_=10, gate=100)
    free = Backtester(costs=DeliveryCosts(brokerage_rate=0.0)).run(df, strat)
    # brutal costs version
    costly = Backtester(costs=DeliveryCosts(brokerage_rate=0.001, brokerage_cap=2000, slippage=0.005)).run(df, strat)
    assert len(costly.trades) >= 1
    assert costly.equity.iloc[-1] < free.equity.iloc[-1]


def test_no_entry_before_warmup_and_no_lookahead_entry_date_after_signal():
    df = flat_then_rising_df()
    bt = Backtester(atr_period=14)
    res = bt.run(df, DonchianBreakout(entry=20, exit_=10, gate=100))
    assert res.trades
    for t in res.trades:
        i = df.index.get_loc(t.entry_date)
        assert i >= 21  # donchian window + gate warmup
        # entry must happen at bar i's OPEN (with slippage), never bar i-1's close:
        assert abs(t.entry_price - df["open"].iloc[i] * (1 + 0.0005)) < 1e-6


def test_flat_market_trades_little():
    idx = pd.bdate_range("2022-01-03", periods=400)
    df = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 1e6}, index=idx)
    res = Backtester().run(df, TrendSMA(200))
    # close never strictly exceeds SMA200 on a flat line: no entries, only warmup artifacts
    assert len(res.trades) <= 1
    assert abs(res.equity.iloc[-1] - res.equity.iloc[0]) / res.equity.iloc[0] < 0.02


def test_equity_starts_at_capital_and_is_continuous():
    df = rising_df()
    res = Backtester(capital=500_000).run(df, TrendSMA(200))
    assert res.equity.iloc[0] == pytest.approx(500_000, rel=0.01)
    assert res.equity.notna().all()
