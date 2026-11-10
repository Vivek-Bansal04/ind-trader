import numpy as np
import pandas as pd
import pytest

from indtrader.backtest.costs import DeliveryCosts
from indtrader.backtest.portfolio import PortfolioBacktester
from indtrader.backtest.strategies import TrendSMA

IDX = pd.bdate_range("2021-01-04", periods=600)


def mk(drift, seed, n=600, start=100.0):
    r = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(r.normal(drift, 0.012, n)))
    hi = np.abs(r.normal(0.006, 0.003, n))
    lo = np.abs(r.normal(0.006, 0.003, n))
    return pd.DataFrame({"open": close * (1 + r.normal(0, 0.002, n)), "high": close * (1 + hi),
                         "low": close * (1 - lo), "close": close, "volume": r.uniform(5e5, 2e6, n)},
                        index=IDX)


DATA_UP = {"A": mk(0.0009, 1), "B": mk(0.0007, 2), "C": mk(0.0008, 3)}
BENCH_UP = mk(0.0008, 9)


def test_trades_enter_at_next_open_no_lookahead():
    res = PortfolioBacktester(capital=1_000_000, use_regime=False).run(DATA_UP, TrendSMA(200))
    assert res.trades
    for t in res.trades:
        s = DATA_UP[t.symbol]
        i = s.index.get_loc(t.entry_date)
        assert i > 0
        assert t.entry_price == pytest.approx(s["open"].iloc[i] * (1 + 0.0005), rel=1e-9)


def test_equity_continuous_and_starts_at_capital():
    res = PortfolioBacktester(capital=500_000, use_regime=False).run(DATA_UP, TrendSMA(200))
    assert res.equity.iloc[0] == pytest.approx(500_000, rel=0.001)
    assert res.equity.notna().all() and (res.equity > 0).all()


def test_positions_cap_and_exposure_ceiling():
    data = {f"S{i}": mk(0.0009, i) for i in range(8)}
    res = PortfolioBacktester(capital=1_000_000, max_positions=3, max_alloc_frac=0.2,
                              use_regime=False).run(data, TrendSMA(200))
    assert res.n_open.max() <= 3
    assert res.exposure.max() <= 3 * 0.2 + 0.05  # cap * alloc + slippage/cost slack


def test_regime_off_scales_exposure_down():
    # bench crashes while names still hold signals -> breadth/idx regime throttles
    bench_down = mk(-0.002, 42)
    kwargs = dict(capital=1_000_000, max_positions=5, max_alloc_frac=0.2)
    on = PortfolioBacktester(use_regime=False, **kwargs).run(DATA_UP, TrendSMA(200))
    off = PortfolioBacktester(use_regime=True, bench_closes=bench_down["close"], **kwargs).run(
        DATA_UP, TrendSMA(200))
    assert off.exposure.mean() < on.exposure.mean()


def test_empty_raises():
    with pytest.raises(ValueError):
        PortfolioBacktester().run({}, TrendSMA(200))
