import pandas as pd
import pytest

from indtrader.data import macrocal
from indtrader.data.store import BarStore


@pytest.fixture
def store(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")
    yield s
    s.close()


def master_row(day="15-Jan-2026", desc="Municipal Corporation Election"):
    # shape copied from the 2026-11-11 live 200-response of /api/holiday-master
    return {"tradingDate": day, "weekDay": "Thursday", "description": desc,
            "morning_session": None, "evening_session": None, "Sr_no": 1}


def bar(store, day):
    c = [100.0]
    store.upsert_candles("NIFTY", "daily",
                         pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                                       "volume": [1e5]},
                                      index=pd.DatetimeIndex([day])), "test")


# ---- fetch + store --------------------------------------------------------------------

def test_fetch_calendar_stores_confirmed_shape(store, monkeypatch):
    monkeypatch.setattr(macrocal, "get_json",
                        lambda url, referer=None: ({"CBM": [master_row()]}, None))
    res = macrocal.fetch_calendar(store)
    assert res["ok"] and res["rows_stored"] == 1
    assert res["transport"] in ("curl_cffi", "urllib")
    assert "tradingDate" in res["row_keys"]
    assert macrocal.iso_trading_day("2026-01-15", store) is False


def test_fetch_failure_keeps_store_and_says_so(store, monkeypatch):
    monkeypatch.setattr(macrocal, "get_json",
                        lambda url, referer=None: ({"CBM": [master_row()]}, None))
    macrocal.fetch_calendar(store)
    monkeypatch.setattr(macrocal, "get_json", lambda url, referer=None: (None, "HTTP 403"))
    res = macrocal.fetch_calendar(store)
    assert not res["ok"] and "403" in res["error"]
    assert macrocal.calendar_status(store) is not None   # old rows survive


def test_shape_drift_refuses_to_store(store, monkeypatch):
    monkeypatch.setattr(macrocal, "get_json",
                        lambda url, referer=None: ({"CBM": [{"weird": "x"}]}, None))
    res = macrocal.fetch_calendar(store)
    assert not res["ok"] and "tradingDate" in res["error"]
    assert macrocal.calendar_status(store) is None


def test_missing_cbm_block_is_an_error_not_zero(store, monkeypatch):
    monkeypatch.setattr(macrocal, "get_json",
                        lambda url, referer=None: ({"data": {"Other": []}}, None))
    res = macrocal.fetch_calendar(store)
    assert not res["ok"] and "unexpected shape" in res["error"]


# ---- the three-state contract -----------------------------------------------------------

def test_unknown_is_never_holiday(store):
    assert macrocal.iso_trading_day("2030-01-01", store) is None   # nothing stored
    macrocal.store_cbm(store, {"2030-01-01": "New Year"})
    assert macrocal.iso_trading_day("2030-01-01", store) is False  # listed holiday
    assert macrocal.iso_trading_day("2030-01-02", store) is None   # absent proves nothing


def test_bar_beats_holiday_row(store):
    macrocal.store_cbm(store, {"2026-02-19": "Maha Shivratri"})
    bar(store, "2026-02-19")                        # the round-5 disputed signature
    assert macrocal.iso_trading_day("2026-02-19", store) is True
    assert macrocal.disputed_dates(store) == ["2026-02-19"]  # and it stays on the ledger


def test_disputed_ledger_only_lists_dates_with_bars(store):
    macrocal.store_cbm(store, {"2026-12-25": "Christmas", "2026-02-19": "Shivratri"})
    bar(store, "2026-02-19")
    assert macrocal.disputed_dates(store) == ["2026-02-19"]


def test_nse_session_calendar_subtracts_stored_holidays(store):
    for d in ("2027-01-01", "2027-01-04", "2027-01-07"):
        bar(store, d)
    macrocal.store_cbm(store, {"2027-01-04": "Some holiday"})   # bar AND holiday row
    cal, src = macrocal.nse_session_calendar(store, 2027)
    assert src == "NIFTY bars minus stored holidays"
    assert [str(d.date()) for d in cal] == ["2027-01-01", "2027-01-07"]


def test_bars_only_calendar_and_none_case(store):
    bar(store, "2027-01-01")
    cal, src = macrocal.nse_session_calendar(store, 2027)
    assert src == "NIFTY bars (store)" and len(cal) == 1
    cal2, src2 = macrocal.nse_session_calendar(store, 2099)
    assert cal2 is None and src2 == "no calendar source"
