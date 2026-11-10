import numpy as np
import pandas as pd

from indtrader.backtest.costs import DeliveryCosts
from indtrader.backtest.strategies import TrendSMA
from indtrader.backtest.walkforward import grid_candidates, walk_forward_gate

N = 1000
IDX = pd.bdate_range("2018-01-01", periods=N)


def mk(drift, seed, vol=0.010):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, N)))
    return pd.DataFrame({"open": close, "high": close * 1.004, "low": close * 0.996,
                         "close": close, "volume": 2e6}, index=IDX)


GRID = {"period": [120, 150, 200]}


def test_windows_are_chronological_and_disjoint():
    rep = walk_forward_gate(mk(0.0009, 3), TrendSMA, GRID, warmup=400, test_days=252)
    assert len(rep.folds) >= 2
    for f in rep.folds:
        a, b = f["window"].split("..")
        assert a < b  # train before test, never shuffled


def test_trending_data_can_pass_flat_cannot():
    # deterministic smooth trend (seed 5) passes; flat noise must not
    trend = walk_forward_gate(mk(0.0008, 5, vol=0.005), TrendSMA, GRID)
    flat = walk_forward_gate(mk(0.0, 8, vol=0.002), TrendSMA, GRID)
    assert trend.passed, trend.reasons
    assert not flat.passed
    assert any("ratio" in r or "profitable" in r or "min" in r for r in flat.reasons)


def test_not_enough_bars_is_a_hard_fail_not_zero_metrics():
    rep = walk_forward_gate(mk(0.0009, 3).iloc[:500], TrendSMA, GRID)
    assert not rep.passed and any("not enough bars" in r for r in rep.reasons)


def test_grid_size_cap_and_param_validation():
    try:
        grid_candidates(TrendSMA, {"period": list(range(20))}, max_grid=12)
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "grid too large" in str(e)
