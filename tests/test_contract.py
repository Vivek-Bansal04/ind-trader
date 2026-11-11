from datetime import date

import pandas as pd
import pytest

from indtrader.data.contract import bhav_close_map, contract_check, holidays_calendar, load_bhavcopy

# Fixture = the REAL bhavcopy shape captured live by the round-2 MacBook probe
# (BhavCopy_NSE_CM_*_20261109, 34 columns; abbreviated to the ones we use).
BHAV_COLS = ["TradDt", "ISIN", "TckrSymb", "SctySrs", "FinInstrmTp", "ClsPric", "PrvsClsgPric"]


def bhav_df(rows):
    return pd.DataFrame(rows, columns=BHAV_COLS)


class FakeStore:
    def __init__(self, frames: dict):
        self.frames = {k: v for k, v in frames.items()}

    def load(self, sym, **kw):
        return self.frames.get(sym, pd.DataFrame())


def store_frame(days=("2026-11-09", "2026-11-10"), close=100.0):
    return pd.DataFrame({"close": [close] * len(days)},
                        index=pd.to_datetime(days))


# ---- bhav_close_map -----------------------------------------------------------

def test_bhav_close_map_uses_probed_column_names():
    df = bhav_df([["09-Nov-2026", "INE000A01018", "RELIANCE", "EQ", "EQI", 1324.4, 1310.0],
                  ["09-Nov-2026", "INE040A01034", "HDFCBANK", "EQ", "EQI", 798.15, 795.0]])
    assert bhav_close_map(df) == {"RELIANCE": 1324.4, "HDFCBANK": 798.15}


def test_bhav_close_map_refuses_when_contract_changed():
    df = pd.DataFrame({"SYMBOL": ["X"], "CLOSE": [1.0]})  # OLD guessed names
    with pytest.raises(KeyError, match="TckrSymb"):  # names the missing cols, never returns {}
        bhav_close_map(df)


def test_bhav_close_map_skips_nan_price_rows():
    df = bhav_df([["09-Nov-2026", "I", "GHOST", "BE", "EQI", float("nan"), 1.0],
                  ["09-Nov-2026", "I", "REAL", "EQ", "EQI", 10.0, 9.0]])
    assert bhav_close_map(df) == {"REAL": 10.0}


# ---- contract_check -------------------------------------------------------------

def test_contract_check_pass_and_mismatch_are_separate():
    store = FakeStore({"RELIANCE": store_frame(close=1324.4), "TCS": store_frame(close=3000.0)})
    closes = {"RELIANCE": 1324.4, "TCS": 3100.0, "NOTINSTOR": 5.0}
    res = contract_check(store, ["RELIANCE", "TCS", "NOTINSTOR"], closes, "2026-11-10")
    assert res["checked"] == 2
    assert res["mismatched"] == ["TCS ours=3000.0 bhav=3100.0"]
    assert res["store_missing_session"] == []


def test_contract_check_names_store_symbols_missing_the_session():
    store = FakeStore({"OLD": store_frame(days=("2026-11-01",), close=10.0)})
    res = contract_check(store, ["OLD"], {"OLD": 10.0}, "2026-11-10")
    assert res["store_missing_session"] == ["OLD"] and res["checked"] == 0


# ---- load_bhavcopy: path-vs-frame duality (round-2 fact) -------------------------

def test_load_bhavcopy_accepts_csv_path_and_frame(tmp_path):
    p = tmp_path / "BhavCopy_NSE_CM_0_0_0_20261109_F_0000.csv"
    bhav_df([["09-Nov-2026", "I", "RELIANCE", "EQ", "EQI", 1324.4, 1310.0]]).to_csv(p, index=False)

    class ClientPath:
        def equity_bhavcopy(self, d):
            return p

    class ClientFrame:
        def equity_bhavcopy(self, d):
            return bhav_df([["09-Nov-2026", "I", "X", "EQ", "EQI", 1.0, 1.0]])

    assert bhav_close_map(load_bhavcopy(ClientPath(), date(2026, 11, 9))) == {"RELIANCE": 1324.4}
    assert bhav_close_map(load_bhavcopy(ClientFrame(), date(2026, 11, 9))) == {"X": 1.0}


# ---- holidays_calendar: the round-2 live shape as fixture -------------------------

def holidays_client():
    class Client:
        def holidays(self):
            return {"CBM": [
                {"tradingDate": "15-Jan-2026", "weekDay": "Thursday", "description": "Municipal Corp Election"},
                {"tradingDate": "26-Jan-2026", "weekDay": "Monday", "description": "Republic Day"},
                {"tradingDate": "14-Aug-2025", "weekDay": "Friday", "description": "other year"},
            ]}
    return Client()


def test_holidays_calendar_parses_dd_Mon_yyyy_and_filters_year():
    hol = holidays_calendar(holidays_client(), 2026)
    assert hol == {"2026-01-15": "Municipal Corp Election", "2026-01-26": "Republic Day"}


def test_holidays_calendar_refuses_unknown_shape():
    class Client:
        def holidays(self):
            return ["surprise"]
    with pytest.raises(RuntimeError, match="unexpected holidays"):
        holidays_calendar(Client(), 2026)


def test_holidays_calendar_refuses_empty_parse():
    with pytest.raises(RuntimeError, match="parsed empty"):
        holidays_calendar(holidays_client(), 2030)


# ---- CLI wiring (nse lib absent on the office box -> honest refusal) --------------

def test_cli_contract_test_and_holidays_refuse_without_lib(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    from indtrader.cli import main

    import indtrader.data.nseapi_provider as P

    monkeypatch.setattr(P.NseIndiaApiProvider, "available", staticmethod(lambda: False))
    assert main(["contract-test"]) == 2
    assert "needs the nse lib" in capsys.readouterr().err
    assert main(["holidays"]) == 2
    get_settings.cache_clear()
