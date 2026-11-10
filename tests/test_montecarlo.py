import numpy as np
import pandas as pd

from indtrader.backtest.costs import DeliveryCosts
from indtrader.backtest.montecarlo import MIN_TRADES, bootstrap_paths, monte_carlo, summarize

IDX = pd.bdate_range("2018-01-01", periods=1000)


def mk(drift, seed, n=1000, start=100.0, vol=0.012):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    return pd.DataFrame({"open": close, "high": close * 1.004, "low": close * 0.996,
                         "close": close, "volume": 2e6}, index=IDX[:n])


class FakeTrade:
    def __init__(self, pnl):
        self.pnl, self.exit_price = pnl, 100.0


class FakeResult:
    def __init__(self, pnls):
        self.trades = [FakeTrade(p) for p in pnls]
        self.equity = pd.Series([1e6], index=IDX[:1])


def test_bootstrap_deterministic_and_shape():
    pnl = list(np.linspace(-8000, 12000, 40))
    p1 = bootstrap_paths(pnl, 1e6, n_sims=200, seed=7)
    p2 = bootstrap_paths(pnl, 1e6, n_sims=200, seed=7)
    assert p1.shape == (200, 40)
    assert np.array_equal(p1, p2)  # seeded => reproducible audit trail
    s = summarize(p1, 1e6)
    assert s["prob_loss"] > 0 and s["maxdd_worst5pct"] <= s["maxdd_median"] < 0


def test_refuses_small_samples_loudly():
    assert bootstrap_paths([1000.0] * (MIN_TRADES - 1), 1e6) is None
    out = monte_carlo(FakeResult([1000.0] * 5))
    assert out["ok"] is False and "refuse to bootstrap" in out["reason"]


def test_positive_edge_shifts_distribution_right():
    weak = monte_carlo(FakeResult(list(np.linspace(-6000, 6000, 60))))
    strong = monte_carlo(FakeResult(list(np.linspace(1000, 15000, 60))))
    assert strong["ok"] and weak["ok"]
    assert strong["final_p50"] > weak["final_p50"]
    assert strong["prob_loss"] < weak["prob_loss"]


def test_nan_trades_are_dropped_not_counted():
    r = FakeResult([1000.0] * 25 + [float("nan")])
    r.trades.append(FakeTrade(float("nan")))
    out = monte_carlo(r)
    assert out["ok"] and out["trades_per_sim"] == 25  # the NaN trade is excluded
