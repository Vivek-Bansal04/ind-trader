import numpy as np
import pandas as pd
import pytest

from indtrader.portfolio.weights import cap_weights, clean_returns, inverse_vol, weights

IDX = pd.bdate_range("2024-01-01", periods=300)


def series(vol, seed, start=100.0):
    rng = np.random.default_rng(seed)
    return pd.Series(start * np.exp(np.cumsum(rng.normal(0.0003, vol, len(IDX)))), index=IDX)


PRICES = {"CALM": series(0.006, 1), "WILD": series(0.030, 2), "MID": series(0.014, 3)}


def test_inverse_vol_rewards_calmness_and_respects_cap():
    rets = clean_returns(PRICES)
    raw = inverse_vol(rets, max_w=0.95)  # pre-cap ordering: calmest gets most
    assert raw["CALM"] > raw["MID"] > raw["WILD"]
    w = inverse_vol(rets, max_w=0.25)
    assert all(v <= 0.25 + 1e-9 for v in w.values())
    assert sum(w.values()) <= 1.0 + 1e-9  # residual is cash, never negative leverage


def test_cap_redistribution_is_sane():
    w = cap_weights({"A": 0.8, "B": 0.1, "C": 0.1}, 0.4)
    assert w["A"] == pytest.approx(0.4)
    assert w["B"] > 0.1 and w["C"] > 0.1 and sum(w.values()) == pytest.approx(1.0)


def test_thin_history_and_flat_series_refuse():
    short = {k: v.iloc[:30] for k, v in PRICES.items()}
    with pytest.raises(ValueError, match="refusing to size on thin air"):
        clean_returns(short)
    flat = dict(PRICES, FLAT=pd.Series(100.0, index=IDX))
    with pytest.raises(ValueError, match="zero-variance"):
        inverse_vol(clean_returns(flat))


def test_hrp_available_or_honest_fallback_and_unknown_method():
    w, used = weights(PRICES, method="hrp")
    assert used in ("hrp", "inverse_vol (fallback)")  # whichever, it tells the truth
    assert all(v <= 0.25 + 1e-9 for v in w.values())
    assert sum(w.values()) <= 1.0 + 1e-9  # 3 names at 25% cap => residual cash is correct
    with pytest.raises(ValueError, match="unknown method"):
        weights(PRICES, method="genius")
