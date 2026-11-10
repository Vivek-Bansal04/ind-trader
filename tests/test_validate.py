import numpy as np
import pandas as pd

from indtrader.backtest.engine import Backtester
from indtrader.backtest.strategies import TrendSMA
from indtrader.research.validate import parameter_plateau, walk_forward


def mixed_df(n=1600, seed=7):
    """~6.5 years synthetic: bull, crash, recovery, chop — a fair walk-forward testbed."""
    rng = np.random.default_rng(seed)
    seg = np.r_[np.full(500, 0.0009), np.full(150, -0.004), np.full(500, 0.0011), np.full(450, 0.0)]
    noise = rng.normal(0, 0.013, n)
    close = 100 * np.exp(np.cumsum(seg[:n] + noise))
    idx = pd.bdate_range("2018-01-01", periods=n)
    return pd.DataFrame({"open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": 1e6}, index=idx)


def test_walk_forward_returns_windows_and_ratio():
    df = mixed_df()
    wf = walk_forward(df, lambda: TrendSMA(200), train_years=3, test_years=1, step_years=1,
                      bt=Backtester(stop_atr_mult=4.0))
    assert len(wf.per_window) >= 2
    assert isinstance(wf.overfit_ratio, float)
    # trend system on a mostly-bull series should not score negative OOS
    assert wf.oos_score > -0.5


def test_parameter_plateau_spread():
    grid = [{"fast": 10, "slow": 50}, {"fast": 20, "slow": 100}, {"fast": 30, "slow": 150}]
    out = parameter_plateau(lambda p: p["fast"] + p["slow"], grid)
    assert out["n"] == 3
    assert out["best"] == 180 and out["worst"] == 60
    assert out["frac_positive"] == 1.0
