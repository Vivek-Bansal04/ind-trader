import numpy as np
import pandas as pd
import pytest

from indtrader.data.store import BarStore
from indtrader.scheduler import etl

IDX = pd.bdate_range("2024-01-01", periods=6)


def candles(close, idx=None):
    close = np.asarray(close, float)
    idx = IDX if idx is None else idx[: len(close)]
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                         "volume": 1e5}, index=idx)


def store_at(tmp_path):
    s = BarStore(tmp_path / "bars.sqlite")
    s.upsert_candles("SBIN", "daily", candles([100, 100, 100, 50, 50, 50]), "test")  # 1:2 split day 4
    return s


def test_split_neutral_after_adjust(tmp_path):
    s = store_at(tmp_path)
    # yfinance-style factor: adj/close == 0.5 before the split, 1.0 after
    s.upsert_adj_factors("SBIN", pd.Series([0.5, 0.5, 0.5, 1.0, 1.0, 1.0], index=IDX), "test")
    adj = s.load("SBIN", adjusted=True)
    assert adj["close"].iloc[-1] == pytest.approx(50.0)          # latest stays the real price
    assert np.allclose(adj["close"], [50, 50, 50, 50, 50, 50])   # split removed -> flat, as true
    raw = s.load("SBIN")
    assert raw["close"].iloc[0] == 100.0                          # raw untouched
    s.close()


def test_no_factors_returns_raw_and_says_so(tmp_path, capsys):
    s = store_at(tmp_path)
    adj = s.load("SBIN", adjusted=True)
    assert np.allclose(adj["close"], [100, 100, 100, 50, 50, 50])
    assert "returning RAW prices" in capsys.readouterr().out       # loud, not silent
    s.close()


def test_upsert_idempotent(tmp_path):
    s = store_at(tmp_path)
    f = pd.Series([0.5, 0.5, 0.5, 1.0, 1.0, 1.0], index=IDX)
    assert s.upsert_adj_factors("SBIN", f, "test") == 6
    assert s.upsert_adj_factors("SBIN", f, "test") == 6
    assert len(s.adj_factors("SBIN")) == 6
    s.close()


def test_etl_stores_factors_and_reports_gaps(tmp_path, monkeypatch):
    class FakeProv:
        name = "fake"

        def daily_adj_factors(self, sym, start, end):
            if sym == "B":
                return pd.Series(dtype="float64")   # no factor data available
            return pd.Series([1.0, 1.0], index=pd.bdate_range("2024-01-01", periods=2))

    monkeypatch.setattr(etl, "YFinanceProvider", lambda: FakeProv())
    s = BarStore(tmp_path / "bars.sqlite")
    s.upsert_candles("A", "daily", candles([1, 2, 3], pd.bdate_range("2024-01-01", periods=3)), "t")
    s.upsert_candles("B", "daily", candles([1, 2, 3], pd.bdate_range("2024-01-01", periods=3)), "t")
    res = etl.fetch_adj_factors(["A", "B"], store=s, pause_s=0)
    assert res["A"] == 2 and res["B"] == 0          # gap reported as gap, not factor=1
    assert len(s.adj_factors("A")) == 2
    s.close()
