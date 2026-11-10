import json

import pytest

from indtrader.data.store import BarStore
from indtrader.research import shareholding as sh


@pytest.fixture
def store(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")
    yield s
    s.close()


def record_quarters(s):
    sh.record(s, "TATA", "Q1FY26", promoter=65.0, fii=12.0, dii=8.0, public=15.0, pledge=0.0)
    sh.record(s, "TATA", "Q2FY26", promoter=62.1, fii=8.5, dii=8.2, public=21.2, pledge=1.5)


def test_deltas_flag_promoter_drop_fii_exit_pledge(store):
    record_quarters(store)
    d = sh.deltas(store, "TATA")
    assert len(d) == 1
    assert set(d[0]["flags"]) == {"promoter -2.90%", "FII exit -3.50%", "pledge +1.50%"}
    assert sh.holding_alerts(store, ["TATA", "QUIET"]) == [
        "TATA Q2FY26: promoter -2.90%, FII exit -3.50%, pledge +1.50%"]


def test_no_quiet_false_positives(store):
    sh.record(store, "CALM", "Q1", promoter=50.0, fii=10.0, dii=10.0, public=30.0, pledge=0.0)
    sh.record(store, "CALM", "Q2", promoter=50.1, fii=9.0, dii=10.0, public=30.9, pledge=0.0)
    assert sh.holding_alerts(store, ["CALM"]) == []


def test_try_fetch_stores_raw_and_reports_keys_never_parses(store):
    class FakeNSE:
        def shareholding(self, symbol):
            return {"data": [{"someOddKey": 1, "another": 2}]}

    out = sh.try_fetch(store, "tata", client=FakeNSE())
    assert out["ok"] and out["method"] == "shareholding" and out["stored_raw"]
    assert out["row_keys"] == ["another", "someOddKey"]           # the answer the parser waits for
    raw = store.con.execute("SELECT payload FROM shareholding_raw").fetchone()[0]
    assert json.loads(raw)["data"][0]["someOddKey"] == 1
    # normalized table stays EMPTY — unknown shape never becomes rows
    assert sh.rows(store, "TATA").empty


def test_try_fetch_reports_missing_method(store):
    class Empty:
        pass

    out = sh.try_fetch(store, "TATA", client=Empty())
    assert not out["ok"] and "no shareholding method" in out["error"]
