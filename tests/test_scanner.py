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


def _flat_volume(n=300, v=2e6):
    idx = pd.bdate_range("2023-01-01", periods=n)
    return pd.Series(v, index=idx)


def test_illiquid_name_dropped_even_when_trend_is_perfect():
    win = mk(300, 0.002, seed=2)
    # price ~150 avg; 2e6 sh/day * ~150 = ~30Cr -> comfortably above the 2Cr floor
    good = scan({"W": win}, bench=mk(300, 0.0003, seed=9), volumes={"W": _flat_volume()})
    assert bool(good.iloc[0]["tradable"]) and good.iloc[0]["turnover_20d"] > 2e7
    # 200 sh/day * ~150 = ~30k/day -> far under the floor
    ill = scan({"W": win}, bench=mk(300, 0.0003, seed=9), volumes={"W": _flat_volume(v=200)})
    assert not bool(ill.iloc[0]["tradable"]) and "illiquid" in ill.iloc[0]["note"]
    assert ill.iloc[0]["turnover_20d"] < 2e7


def test_unknown_volume_never_disqualifies():
    win = mk(300, 0.002, seed=2)
    out = scan({"W": win}, bench=mk(300, 0.0003, seed=9))  # no volumes passed at all
    assert bool(out.iloc[0]["tradable"]) and pd.isna(out.iloc[0]["turnover_20d"])


def test_one_volume_spike_cannot_launder_an_illiquid_name():
    win = mk(300, 0.002, seed=2)
    v = _flat_volume(v=200)
    v.iloc[-1] = 2e5  # 1000x day — a printed print; 20-day mean is still far under floor
    out = scan({"W": win}, bench=mk(300, 0.0003, seed=9), volumes={"W": v})
    assert not bool(out.iloc[0]["tradable"])


def test_flip_note_names_thresholds_and_counts():
    from indtrader.research.regime import Regime, flip_note

    assert flip_note(None) == ""
    on = flip_note(Regime("ON", 1.0, 0.7, True, index_sma200=24000.0, universe_n=100))
    assert "breadth 70%" in on and "35%" in on and "35 of 100" in on
    off = flip_note(Regime("OFF", 0.3, 0.2, False, index_sma200=24000.0, universe_n=100))
    assert "60%" in off and "40 more of 100" in off and "24,000" in off
    neut = flip_note(Regime("NEUTRAL", 0.6, None, None))
    assert "breadth unknown" in neut  # unknown stated as unknown, not assumed fine


def _bars(c):
    return pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
                         "volume": 2e6}, index=c.index)


def test_compressed_base_annotates_never_boosts():
    c = mk(300, 0.002, seed=2)
    base = float(c.iloc[-21])
    tail = pd.Series(base * (1.0 + 0.0002 * np.sin(np.arange(20))), index=c.index[-20:])
    sq = pd.concat([c.iloc[:-20], tail])
    out = scan({"W": sq}, bench=mk(300, 0.0003, seed=9), volumes={"W": _flat_volume()},
               bars={"W": _bars(sq)})
    row = out.iloc[0]
    assert row["base_quality"] == "tight" and "compressed base" in row["note"]
    assert bool(row["tradable"])
    plain = scan({"W": sq}, bench=mk(300, 0.0003, seed=9), volumes={"W": _flat_volume()})
    assert plain.iloc[0]["score"] == row["score"]  # annotation, never a boost
    assert pd.isna(plain.iloc[0]["base_quality"])  # no bars passed -> unknown, not "wide"


def test_wide_name_shows_wide_not_tight():
    c = mk(300, 0.002, seed=2)
    out = scan({"W": c}, bench=mk(300, 0.0003, seed=9), volumes={"W": _flat_volume()},
               bars={"W": _bars(c)})
    assert out.iloc[0]["base_quality"] == "wide"
