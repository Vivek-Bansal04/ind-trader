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
