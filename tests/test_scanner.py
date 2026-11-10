import numpy as np
import pandas as pd

from indtrader.research.regime import Regime
from indtrader.research.scanner import evidence_row, scan


def mk(n=300, drift=0.0006, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-01", periods=n)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n))), index=idx)


def test_uptrend_strong_name_ranks_first():
    bench = mk(300, 0.0003, seed=9)
    winner = mk(300, 0.002, seed=2)   # strong uptrend, beats bench
    # deterministic downtrend so the test never depends on seed luck
    loser = pd.Series(np.linspace(200, 100, 300),
                      index=pd.bdate_range("2023-01-01", periods=300))
    out = scan({"WIN": winner, "LOSE": loser}, bench)
    assert list(out["symbol"]) == ["WIN", "LOSE"]
    top = out.iloc[0]
    assert bool(top["above_200dma"]) and bool(top["tradable"])  # pandas stores numpy bools
    assert top["mom_6m"] > 0
    assert not bool(out.iloc[1]["tradable"])


def test_short_history_marked_not_tradable():
    row = evidence_row("NEW", mk(50), None)
    assert row["tradable"] is False
    assert "bars" in row["note"]


def test_regime_scale_attached():
    bench = mk(seed=9)
    out = scan({"A": mk(seed=2)}, bench, regime=Regime("OFF", 0.3, 0.2, False))
    assert out["exposure_scale"].iloc[0] == 0.3


def test_no_bench_still_works():
    out = scan({"A": mk(seed=2), "B": mk(seed=4)}, bench=None)
    assert set(out["quadrant"]) == {"NA"}
    assert len(out) == 2
