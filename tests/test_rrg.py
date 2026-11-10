import numpy as np
import pandas as pd

from indtrader.research.rrg import quadrant, rrg_table, rs_momentum, rs_ratio, transitions


def series(n, slope):
    idx = pd.date_range("2023-01-06", periods=n, freq="W-FRI")
    return pd.Series(100 * np.exp(np.cumsum(np.full(n, slope))), index=idx)


def test_outperformer_is_leading():
    stock, bench = series(120, 0.01), series(120, 0.002)
    rsr = rs_ratio(stock, bench, m=12).dropna()
    rsm = rs_momentum(rsr, m=12, k=6).dropna()
    assert rsr.iloc[-1] > 100
    assert quadrant(float(rsr.iloc[-1]), float(rsm.iloc[-1])) == "Leading"


def test_underperformer_is_lagging():
    stock, bench = series(120, -0.004), series(120, 0.004)
    rsr = rs_ratio(stock, bench, m=12).dropna()
    rsm = rs_momentum(rsr, m=12, k=6).dropna()
    assert quadrant(float(rsr.iloc[-1]), float(rsm.iloc[-1])) == "Lagging"


def test_rrg_table_shape():
    bench = series(80, 0.003)
    table = rrg_table({"WIN": series(80, 0.008), "LOSE": series(80, -0.002)}, bench, m=12, k=6)
    assert list(table["symbol"]) == ["WIN", "LOSE"]  # sorted by rs_ratio desc
    assert table["quadrant"].iloc[0] in {"Leading", "Weakening", "Improving", "Lagging"}


def test_transitions_detect_change():
    idx = pd.date_range("2024-01-05", periods=5)
    hist = pd.DataFrame({
        "dt": idx,
        "symbol": "X",
        "quadrant": ["Lagging", "Lagging", "Improving", "Leading", "Weakening"],
    })
    t = transitions(hist, "X")
    assert ("Lagging", "Improving") in t
    assert ("Improving", "Leading") in t
    assert ("Leading", "Weakening") in t
    assert len(t) == 3
