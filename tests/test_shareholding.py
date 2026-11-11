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


def test_try_fetch_stores_raw_and_never_invents_rows(store):
    class FakeNSE:
        def shareholding(self, symbol):
            return {"data": [{"someOddKey": 1, "another": 2}]}

    out = sh.try_fetch(store, "tata", client=FakeNSE())
    assert not out["ok"] and out["raws_stored"] == 1          # raw kept for the record
    joined = "\n".join(out["probed"])
    assert "someOddKey" in joined                              # the shape the parser saw
    assert "not the confirmed summary" in joined
    raw = store.con.execute("SELECT payload FROM shareholding_raw").fetchone()[0]
    assert json.loads(raw)["data"][0]["someOddKey"] == 1
    # normalized table stays EMPTY — unknown shape never becomes rows
    assert sh.rows(store, "TATA").empty


def test_try_fetch_reports_missing_method(store):
    class Empty:
        pass

    out = sh.try_fetch(store, "TATA", client=Empty())
    assert not out["ok"] and "no shareholding method" in out["error"]


# ---- confirmed-shape parser (the round-4 live payload is the fixture) --------------

REL_ROWS = [  # abbreviated from ROUND4-TODO PASTE 4 (real keys, real values)
    {"date": "30-SEP-2026", "symbol": "RELIANCE", "name": "Reliance Industries Limited",
     "desc": "NEW_1", "pr_and_prgrp": "50.44", "public_val": "49.56",
     "employeeTrusts": "0", "recordId": "216074"},
    {"date": "30-JUN-2026", "symbol": "RELIANCE", "desc": "NEW_1",
     "pr_and_prgrp": "50.31", "public_val": "49.69", "employeeTrusts": "0"},
    {"date": "31-MAR-2026", "symbol": "OTHERCO", "desc": "NEW_1",
     "pr_and_prgrp": "1.0", "public_val": "99.0", "employeeTrusts": "0"},
    {"date": "31-DEC-2025", "symbol": "RELIANCE", "desc": "NEW_1",
     "pr_and_prgrp": "-", "public_val": "-", "employeeTrusts": "0"},  # placeholder
]


def test_quarter_label_indian_fy():
    assert sh.quarter_label("30-SEP-2026") == "Q2FY27"
    assert sh.quarter_label("31-MAR-2026") == "Q4FY26"   # FY ends March
    assert sh.quarter_label("30-JUN-2026") == "Q1FY27"
    assert sh.quarter_label("31-DEC-2026") == "Q3FY27"
    with pytest.raises(ValueError):
        sh.quarter_label("2026-09-30")                   # not the payload format


def test_parse_pattern_rows_uses_confirmed_keys_and_skips_noise():
    rows = sh.parse_pattern_rows("RELIANCE", {"data": REL_ROWS})
    # OTHERCO row dropped (different symbol), "-" placeholder skipped, never zeroed
    assert rows == [{"period": "Q2FY27", "promoter": 50.44, "public": 49.56},
                    {"period": "Q1FY27", "promoter": 50.31, "public": 49.69}]


def test_parse_pattern_rows_refuses_when_shape_changed():
    broken = [{"date": "30-SEP-2026", "symbol": "RELIANCE", "promoterPct": 50.0}]
    with pytest.raises(KeyError, match="pr_and_prgrp"):   # names the missing keys
        sh.parse_pattern_rows("RELIANCE", {"data": broken})


def test_try_fetch_now_records_confirmed_pattern(store):
    """Round-4 confirmed the shape; the v2 sweep records pattern rows from
    whichever payload actually carries it — the round-5 detail-view payload
    no longer blocks the good one."""
    class FakeNSE:
        def shareholding(self, symbol):
            return {"data": REL_ROWS}

        def shareholding_details(self, symbol, view=None):
            if view is None:  # the per-holder DETAIL view round 5 fell into
                return {"data": [{"holder": "ABC MUTUAL FUND", "pct": 4.2}]}
            return {"data": REL_ROWS}

    out = sh.try_fetch(store, "RELIANCE", client=FakeNSE())
    assert out["ok"] and out["quarters_recorded"] >= 2
    joined = "\n".join(out["probed"])
    assert "not the confirmed summary" in joined        # detail view: seen, reported, refused
    assert "CONFIRMED shape" in joined                  # summary view: rows recorded
    assert "confirmed shape" in out["note"]
    assert len(sh.rows(store, "RELIANCE")) == 2         # re-ingest replaces, never duplicates
    df = sh.rows(store, "RELIANCE")
    assert list(df["period"]) == ["Q1FY27", "Q2FY27"]
    assert df["promoter"].tolist() == [50.31, 50.44]
    assert df["fii"].isna().all()                        # unknown stays unknown
    # v2 stores EVERY non-empty payload raw: summary + detail + summary-restated
    assert store.con.execute("SELECT count(*) FROM shareholding_raw").fetchone()[0] == 3
