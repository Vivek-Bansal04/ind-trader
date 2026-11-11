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


def test_nseapi_camelcase_keys_accepted(monkeypatch):
    """2026-11-11 live: the archive response moved to camelCase keys (round-3 probe
    printed the real row). Both spellings must parse — a rollback must not read as
    '0 rows of data' again."""
    from indtrader.data.nseapi_provider import NseIndiaApiProvider

    camel = [{
        "mtimestamp": "2026-10-31", "chSymbol": "RELIANCE", "chSeries": "EQ",
        "chOpeningPrice": 1200.0, "chTradeHighPrice": 1234.5, "chTradeLowPrice": 1190.0,
        "chClosingPrice": 1230.25, "chLastTradedPrice": 1230.0,
        "chTotTradedQty": 8123456, "chTotTradedVal": 9.9e9, "chTotalTrades": 45678,
        "ch52WeekHighPrice": 1600.0, "ch52WeekLowPrice": 1100.0,
        "chPreviousClsPrice": 1199.0, "vwap": 1210.0,
    }]

    class FakeNSE:
        def __init__(self, download_folder=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def fetch_equity_historical_data(self, symbol, from_date=None, to_date=None):
            return camel

    import sys
    import types

    fake_mod = types.ModuleType("nse")
    fake_mod.NSE = FakeNSE
    monkeypatch.setitem(sys.modules, "nse", fake_mod)

    df = NseIndiaApiProvider().daily("reliance", "2026-10-01", "2026-10-31")
    assert len(df) == 1
    assert df["close"].iloc[0] == 1230.25
    assert df["open"].iloc[0] == 1200.0


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


def last_settled_session() -> str:
    """Latest weekday strictly before today — 'settled' for the freshness rule
    at ANY wall-clock hour (weekends make the IST clock irrelevant: a Fri bar
    is settled whether it's 14:00 or 17:00)."""
    from datetime import date, timedelta

    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def open_store(tmp_path):
    """No BarStore() default path here — a missing DATA_DIR would silently create
    ~/.indtrader/bars.sqlite during tests."""
    from indtrader.data.store import BarStore

    return BarStore(tmp_path / "b.sqlite")


def seed_nifty(store, last=None):
    """Calendar anchor for the freshness rule: NIFTY sessions 2025-01-02 -> `last`
    (default: the latest settled weekday)."""
    seed(store, "NIFTY", "2025-01-02", last or last_settled_session())


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
    seed(store, "DONE", "2015-01-05", last_settled_session())
    store.close()
    res, prov = run(tmp_path, ["DONE"], skip_fresh=True)
    assert prov.calls == 0                       # provider never touched
    assert "skipped" in capsys.readouterr().out  # and it SAID so
    assert res["DONE"] is None                   # None = skipped-fresh, distinct from 0


def test_stale_tail_refetches_even_when_deep(tmp_path):
    from indtrader.data.store import BarStore

    store = BarStore(tmp_path / "b.sqlite")
    seed(store, "OLD", "2015-01-05", "2026-06-01")   # deep but tail far behind today
    store.close()
    _, prov = run(tmp_path, ["OLD"], skip_fresh=True)
    assert prov.calls == 1


# ---- round-8: missed-evening hole healing (calendar-anchored freshness) ---------

def test_missed_two_evenings_heal_on_next_run(tmp_path):
    """The user's exact question: laptop off Mon+Tue, opened Wed. The store's
    NIFTY bars are the exchange calendar — a symbol whose tail sits TWO sessions
    behind it is a hole of any size and must refetch. The old busday<=3 rule
    called this 'fresh' and left the hole until Thursday."""
    store = open_store(tmp_path)
    seed_nifty(store)                                     # calendar current to settled session
    back2 = pd.bdate_range(end=last_settled_session(), periods=3)[0]
    seed(store, "GAPSYM", "2015-01-05", str(back2.date()))  # 2 sessions behind
    store.close()
    _, prov = run(tmp_path, ["GAPSYM"], skip_fresh=True)
    assert prov.calls == 1                               # healed, not skipped


def test_one_session_behind_also_refetches(tmp_path):
    """Old rule's other hole: a single missed evening stayed 'fresh' for days."""
    store = open_store(tmp_path)
    seed_nifty(store)
    back1 = pd.bdate_range(end=last_settled_session(), periods=2)[0]
    seed(store, "HOLE1", "2015-01-05", str(back1.date()))
    store.close()
    _, prov = run(tmp_path, ["HOLE1"], skip_fresh=True)
    assert prov.calls == 1


def test_calendar_current_symbol_still_skipped(tmp_path, capsys):
    """The other side: tail ON the calendar's latest settled session = genuinely
    complete -> provider untouched (the whole point of skip_fresh stays true)."""
    store = open_store(tmp_path)
    seed_nifty(store)
    seed(store, "DONE", "2015-01-05", last_settled_session())
    store.close()
    res, prov = run(tmp_path, ["DONE"], skip_fresh=True)
    assert prov.calls == 0
    assert res["DONE"] is None
    assert "skipped" in capsys.readouterr().out


def test_fresh_but_shallow_series_still_fetches(tmp_path, capsys):
    """The trap this guard exists for: a fresh-tailed series with barely any
    history is NOT done — declaring it skipped would quietly fake a long
    backtest. (Round-5 correction: "shallow" means <60 bars, not "starts after
    2015" — a 2024 listing with a real tail can never reach back to --start.)"""
    from indtrader.data.store import BarStore

    store = BarStore(tmp_path / "b.sqlite")
    seed(store, "SHALLOW", "2026-10-05", last_settled_session())  # ~26 bars
    store.close()
    _, prov = run(tmp_path, ["SHALLOW"], skip_fresh=True)
    assert prov.calls == 1                       # shallow + not-deep → refetch
    assert "skipped" not in capsys.readouterr().out


def test_new_listing_with_real_tail_is_skipped(tmp_path, capsys):
    """Round-5 live finding: ~193/499 symbols listed after 2015 failed the old
    both-prongs rule EVERY night, so run_once blew past 2 minutes re-fetching
    history that cannot exist (pre-listing). New listing + >=60 bars + current
    tail = complete; the nightly loop must skip it."""
    from indtrader.data.store import BarStore

    store = BarStore(tmp_path / "b.sqlite")
    seed(store, "NEWLIST", "2024-06-03", last_settled_session())  # 600+ bars
    store.close()
    _, prov = run(tmp_path, ["NEWLIST"], skip_fresh=True)
    assert prov.calls == 0
    assert "skipped" in capsys.readouterr().out


def test_skip_fresh_default_off_explicit_fetch_always_fetches(tmp_path):
    from indtrader.data.store import BarStore

    store = BarStore(tmp_path / "b.sqlite")
    seed(store, "DONE", "2015-01-05", last_settled_session())
    store.close()
    _, prov = run(tmp_path, ["DONE"], skip_fresh=False)
    assert prov.calls == 1
