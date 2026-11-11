import pandas as pd

from indtrader.config import Settings
from indtrader.scheduler.etl import build_providers


def test_build_providers_skips_unavailable(monkeypatch):
    # simulate NseIndiaApi not installed
    import indtrader.data.nseapi_provider as mod

    monkeypatch.setattr(mod.NseIndiaApiProvider, "available", staticmethod(lambda: False))
    s = Settings(data_provider_order="nseapi,yfinance")
    names = [p.name for p in build_providers(s)]
    assert names == ["yfinance"]


def test_build_providers_includes_nseapi_when_installed(monkeypatch):
    import indtrader.data.nseapi_provider as mod

    monkeypatch.setattr(mod.NseIndiaApiProvider, "available", staticmethod(lambda: True))
    s = Settings(data_provider_order="nseapi,yfinance")
    names = [p.name for p in build_providers(s)]
    assert names == ["nseapi", "yfinance"]


def test_unknown_provider_falls_back(monkeypatch):
    import indtrader.data.nseapi_provider as mod

    monkeypatch.setattr(mod.NseIndiaApiProvider, "available", staticmethod(lambda: False))
    s = Settings(data_provider_order="nonexistent")
    names = [p.name for p in build_providers(s)]
    assert names == ["yfinance"]  # safe default, never empty chain


def test_nseapi_row_mapping(monkeypatch):
    """Adapter maps NSE archive keys -> canonical OHLCV without touching the network."""
    from indtrader.data.nseapi_provider import NseIndiaApiProvider

    fake_rows = [
        {
            "CH_TIMESTAMP": "2025-04-01",
            "CH_OPENING_PRICE": 1802.0,
            "CH_TRADE_HIGH_PRICE": 1809.9,
            "CH_TRADE_LOW_PRICE": 1765.35,
            "CH_CLOSING_PRICE": 1767.85,
            "CH_TOT_TRADED_QTY": 14255503,
        },
        {"CH_TIMESTAMP": "2025-04-02"},  # partial row must be dropped, not crash
    ]

    class FakeNSE:
        def __init__(self, download_folder=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def fetch_equity_historical_data(self, symbol, from_date=None, to_date=None):
            assert symbol == "HDFCBANK"
            return fake_rows

    import sys
    import types

    fake_mod = types.ModuleType("nse")
    fake_mod.NSE = FakeNSE
    monkeypatch.setitem(sys.modules, "nse", fake_mod)

    df = NseIndiaApiProvider().daily("hdfcbank", "2025-04-01", "2025-04-02")
    assert len(df) == 1
    assert df["close"].iloc[0] == 1767.85
    assert df["volume"].iloc[0] == 14255503


# ---- skip_fresh: resume-friendly re-runs (the 2-hour re-download problem) ------

class CountingProvider:
    name = "fake"

    def __init__(self, rows=5):
        self.calls = 0
        self.rows = rows

    def daily(self, sym, start, end):
        self.calls += 1
        days = pd.bdate_range("2025-06-02", periods=self.rows)
        c = [100.0] * self.rows
        return pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                             "volume": [1e5] * self.rows}, index=pd.DatetimeIndex(days))


def seed(store, sym, first, last):
    days = pd.bdate_range(first, last)
    c = [100.0] * len(days)
    store.upsert_candles(sym, "daily",
                         pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                                       "volume": [1e5] * len(days)},
                                      index=pd.DatetimeIndex(days)), "test")


def run(tmp_path, symbols, skip_fresh, fresh_bdays=3):
    from indtrader.data.store import BarStore
    from indtrader.scheduler.etl import fetch_universe

    prov = CountingProvider()
    store = BarStore(tmp_path / "b.sqlite")
    res = fetch_universe(symbols, store=store, providers=[prov],
                         start="2015-01-01", pause_s=0.0,
                         skip_fresh=skip_fresh, fresh_bdays=fresh_bdays)
    store.close()
    return res, prov


def test_deep_and_fresh_symbol_is_skipped(tmp_path, capsys):
    from indtrader.data.store import BarStore

    store = BarStore(tmp_path / "b.sqlite")
    seed(store, "DONE", "2015-01-05", str(pd.Timestamp.today().date()))
    store.close()
    res, prov = run(tmp_path, ["DONE"], skip_fresh=True)
    assert prov.calls == 0                       # provider never touched
    assert "skipped" in capsys.readouterr().out  # and it SAID so
    assert res["DONE"] == 0


def test_stale_tail_refetches_even_when_deep(tmp_path):
    from indtrader.data.store import BarStore

    store = BarStore(tmp_path / "b.sqlite")
    seed(store, "OLD", "2015-01-05", "2026-06-01")   # deep but tail far behind today
    store.close()
    _, prov = run(tmp_path, ["OLD"], skip_fresh=True)
    assert prov.calls == 1


def test_fresh_but_shallow_series_still_fetches(tmp_path):
    """The trap this guard exists for: a fresh-tailed series starting years after
    --start is NOT done — declaring it skipped would quietly fake a long backtest."""
    from indtrader.data.store import BarStore

    store = BarStore(tmp_path / "b.sqlite")
    seed(store, "SHALLOW", "2024-01-01", str(pd.Timestamp.today().date()))
    store.close()
    _, prov = run(tmp_path, ["SHALLOW"], skip_fresh=True)
    assert prov.calls == 1


def test_skip_fresh_default_off_explicit_fetch_always_fetches(tmp_path):
    from indtrader.data.store import BarStore

    store = BarStore(tmp_path / "b.sqlite")
    seed(store, "DONE", "2015-01-05", str(pd.Timestamp.today().date()))
    store.close()
    _, prov = run(tmp_path, ["DONE"], skip_fresh=False)
    assert prov.calls == 1
