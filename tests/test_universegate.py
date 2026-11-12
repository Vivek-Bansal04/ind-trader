import numpy as np
import pandas as pd
import pytest

from indtrader.backtest.strategies import TrendSMA
from indtrader.backtest.universegate import run_universe_gate
from indtrader.data.store import BarStore

WARM, TESTD = 100, 63
GRID = {"period": [60, 90]}


def _bars(n=600, seed=7, cliff_at=None, factor=0.5):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.006, n)))
    if cliff_at is not None:
        close = close.copy()
        close[cliff_at:] *= factor  # one day of exactly -50%
    dates = pd.bdate_range("2020-01-02", periods=n)
    return pd.DataFrame({"open": close, "high": close * 1.004, "low": close * 0.996,
                         "close": close, "volume": 2e6}, index=dates)


@pytest.fixture
def store(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")
    yield s
    s.close()


def test_clean_gets_gated_gappy_excluded_by_default(store):
    store.upsert_candles("CLEAN", "daily", _bars(), "test")
    store.upsert_candles("GAPPY", "daily", _bars(seed=4, cliff_at=300), "test")
    rep = run_universe_gate(store, ["GAPPY", "CLEAN"], TrendSMA, GRID,
                            warmup=WARM, test_days=TESTD)
    rows = {r.symbol: r for r in rep.rows}
    assert rows["GAPPY"].status == "EXCLUDED" and "-50%" in rows["GAPPY"].detail
    assert rows["CLEAN"].status in ("PASS", "FAIL") and rows["CLEAN"].report is not None
    assert "1 excluded pending factors" in rep.summary_line()
    # the EXCLUDED symbol must NOT have produced a fold report — that's the whole point
    assert rows["GAPPY"].report is None


def test_include_gappy_loudly_runs_anyway(store):
    store.upsert_candles("GAPPY", "daily", _bars(seed=4, cliff_at=300), "test")
    rep = run_universe_gate(store, ["GAPPY"], TrendSMA, GRID, include_gappy=True,
                            warmup=WARM, test_days=TESTD)
    r = rep.rows[0]
    assert r.status in ("PASS", "FAIL") and "DESPITE 1 cliff(s)" in r.detail


def test_explained_cliff_is_not_excluded(store):
    df = _bars(seed=4, cliff_at=300)
    store.upsert_candles("FIXED", "daily", df, "test")
    day = pd.Timestamp(df.index[300]).date().isoformat()
    store.upsert_split_factors("FIXED", {day: 0.5})
    rep = run_universe_gate(store, ["FIXED"], TrendSMA, GRID, warmup=WARM, test_days=TESTD)
    assert rep.rows[0].status in ("PASS", "FAIL")   # explained = gateable, like health says


def test_short_and_missing_are_named_never_failures(store):
    store.upsert_candles("SHORT", "daily", _bars(n=200), "test")
    rep = run_universe_gate(store, ["SHORT", "GHOST"], TrendSMA, GRID,
                            warmup=WARM, test_days=TESTD)
    rows = {r.symbol: r for r in rep.rows}
    assert rows["SHORT"].status == "TOO_SHORT" and "200 bars" in rows["SHORT"].detail
    assert rows["GHOST"].status == "NODATA" and "fetch-universe" in rows["GHOST"].detail
    assert "0 pass" in rep.summary_line()
