import json

import pytest

from indtrader.data.store import BarStore
from indtrader.research import coractions as ca


@pytest.fixture
def store(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")
    yield s
    s.close()


def test_try_fetch_stores_raw_and_reports_keys(store):
    class FakeNSE:
        def fetch_equity_corporate_actions(self, symbol):
            return {"data": [{"someWeirdKey": "17-Nov-2025", "purpose": "Split of shares"}]}

    out = ca.try_fetch(store, "hfcb", client=FakeNSE())
    assert out["ok"] and out["stored_raw"]
    assert out["row_keys"] == ["purpose", "someWeirdKey"]      # the answer the parser waits for
    assert "parse REFUSED" in out["note"]                      # unknown shape -> refusal, not rows
    raw = store.con.execute("SELECT payload FROM corporate_actions_raw").fetchone()[0]
    assert json.loads(raw)["data"][0]["someWeirdKey"] == "17-Nov-2025"


def test_parse_events_accepts_documented_shape_and_refuses_names(store):
    # documented candidate keys -> parses
    events = ca.parse_events("X", {"data": [{"exDate": "26-Aug-2025", "purpose": "Split 1:2"}]})
    assert events == [{"date": "26-Aug-2025", "kind": "Split 1:2", "symbol": "X"}]
    # anything else -> raises naming what it tried AND what it saw
    with pytest.raises(KeyError, match="exDate"):
        ca.parse_events("X", {"data": [{"weirdDate": "x", "why": "y"}]})


def test_try_fetch_reports_missing_method(store):
    class Empty:
        pass

    out = ca.try_fetch(store, "TATA", client=Empty())
    assert not out["ok"] and "no corporate-actions method" in out["error"]


def test_cli_coractions_refuses_without_lib(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    import indtrader.data.nseapi_provider as P

    monkeypatch.setattr(P.NseIndiaApiProvider, "available", staticmethod(lambda: False))
    from indtrader.cli import main

    assert main(["coractions", "RELIANCE"]) == 2
    assert "needs the nse lib" in capsys.readouterr().err
    get_settings.cache_clear()
