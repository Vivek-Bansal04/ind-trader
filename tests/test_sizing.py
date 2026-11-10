import numpy as np
import pandas as pd
import pytest

from indtrader.portfolio.sizing import correlation_penalty, size_position


def test_risk_basis_caps_quantity():
    # equity 10L, risk 1% = 10k, ATR 5, stop mult 3 => per-share risk 15 => qty ~666
    # alloc cap 10% of 10L / price 500 = 200 => alloc binds
    r = size_position(equity=1_000_000, price=500, atr_value=5, cash=10_000_000)
    assert r.qty == 200
    assert r.reason == "alloc"


def test_risk_binding_when_atr_high():
    r = size_position(equity=1_000_000, price=100, atr_value=10, cash=10_000_000)  # per-share 30
    assert r.qty == int(10_000 / 30)  # 333
    assert r.reason == "risk"


def test_corr_penalty_shrinks_size():
    a = size_position(equity=1_000_000, price=100, atr_value=10, cash=10_000_000)
    b = size_position(equity=1_000_000, price=100, atr_value=10, cash=10_000_000, corr_penalty=0.5)
    assert b.qty < a.qty


def test_cash_constraint():
    r = size_position(equity=1_000_000, price=500, atr_value=2, cash=60_000)
    assert 100 <= r.qty <= 120


def test_invalid_inputs_zero():
    assert size_position(equity=1_000_000, price=0, atr_value=5).qty == 0
    assert size_position(equity=1_000_000, price=100, atr_value=0).qty == 0


def test_correlation_penalty_value():
    rng = np.random.default_rng(5)
    rng_dates = pd.date_range("2023-01-01", periods=120)
    r = rng.normal(0.0005, 0.01, 120)
    base = pd.Series(100 * np.cumprod(1 + r), index=rng_dates)
    twin = base * 1.1  # identical returns -> corr = +1
    anti = pd.Series(100 * np.cumprod(1 - r), index=rng_dates)  # mirror -> corr = -1
    assert correlation_penalty(base, [twin]) == pytest.approx(0.5)
    assert correlation_penalty(base, [anti]) == pytest.approx(0.5)  # abs corr counts
    indep = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 120)), index=rng_dates)
    assert correlation_penalty(base, [indep], threshold=0.99) == 0.0
    assert correlation_penalty(base, []) == 0.0
