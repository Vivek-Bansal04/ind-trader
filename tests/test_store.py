import pandas as pd
import pytest

from indtrader.data.store import BarStore


@pytest.fixture()
def store(tmp_path):
    s = BarStore(path=tmp_path / "bars.sqlite")
    yield s
    s.close()


def sample_df():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0}, index=idx)


def test_upsert_and_load_roundtrip(store):
    n = store.upsert_candles("reliance", "daily", sample_df(), "test")
    assert n == 5
    df = store.load("RELIANCE")
    assert len(df) == 5
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_upsert_is_idempotent_and_updates(store):
    store.upsert_candles("TCS", "daily", sample_df(), "test")
    df2 = sample_df()
    df2["close"] = 100.8  # stays under high=101 so the row is still OHLC-valid
    store.upsert_candles("TCS", "daily", df2, "test2")
    df = store.load("TCS")
    assert len(df) == 5
    assert df["close"].iloc[-1] == 100.8
    assert store.load("TCS")["close"].notna().all()


def test_invalid_rows_are_dropped_on_write(store):
    df = sample_df()
    df.iloc[2, df.columns.get_loc("high")] = 98.0  # high < low
    n = store.upsert_candles("INFY", "daily", df, "test")
    assert n == 4
    assert len(store.load("INFY")) == 4


def test_missing_days_and_last_date(store):
    store.upsert_candles("ITC", "daily", sample_df(), "test")
    assert store.missing_days("ITC", start="2024-01-01") == 0
    assert store.last_date("ITC").isoformat().startswith("2024-01-05")
