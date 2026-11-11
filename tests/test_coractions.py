import json

import pytest

from indtrader.data.store import BarStore
from indtrader.research import coractions as ca


@pytest.fixture
def store(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")
    yield s
    s.close()


class FakeNSE:
    """Probe-v2 target: primary method returns [] (round-5 live behaviour), the
    alternate carries pattern-style rows with UNKNOWN keys."""

    def actions(self, symbol, segment=None):
        return []

    def get_corporate_actions(self, symbol):
        return {"data": [{"someWeirdKey": "17-Nov-2025", "purpose": "Split of shares 1:2"}]}


def test_probe_v2_stores_nonempty_payloads_and_reports_every_shape(store):
    out = ca.try_fetch(store, "hfcb", client=FakeNSE())
    assert out["ok"] and out["stored_any"]
    joined = "\n".join(out["probed"])
    assert "empty list" in joined                      # the [] calls are REPORTED, not hidden
    assert "someWeirdKey" in joined                    # row_keys of the real payload = the answer
    assert "PARSED" not in joined                      # unknown shape never pretends to parse
    raws = ca.raw_payloads(store, "HFCB")
    assert len(raws) == 1                              # only NON-empty payloads stored
    assert raws[0][1] == "get_corporate_actions('HFCB')"
    assert json.loads(json.dumps(raws[0][2]))["data"][0]["someWeirdKey"] == "17-Nov-2025"


def test_all_empty_is_not_ok(store):
    class AllEmpty:
        def actions(self, symbol, segment=None):
            return []

    out = ca.try_fetch(store, "X", client=AllEmpty())
    assert not out["ok"] and "every corporate-actions call" in out["error"]
    assert out["probed"]                               # the inventory IS the deliverable


def test_parse_events_waits_for_confirmed_keys(store):
    with pytest.raises(KeyError, match="refuse to guess"):
        ca.parse_events("X", {"data": [{"weirdDate": "x", "why": "y"}]})
    # once the keys are the confirmed candidates, parse works — shape-gated only
    ev = ca.parse_events("X", {"data": [{"date": "17-Nov-2025",
                                         "purpose": "Split of shares 1:2",
                                         "ratio": "0.5"}]})
    assert ev == [{"date": "17-Nov-2025", "kind": "Split of shares 1:2",
                   "ratio": "0.5", "symbol": "X"}]


def test_is_split_kind_never_guesses():
    assert ca.is_split_kind("Split of shares 1:2") and ca.is_split_kind("Bonus issue")
    assert not ca.is_split_kind("Dividend Rs 12")     # dividend is NOT a split factor
    assert not ca.is_split_kind("Board meeting")      # ambiguous -> never a factor


def test_cli_coractions_from_raw_offline(tmp_path, monkeypatch, capsys):
    """--from-raw works with NO nse lib: it replays the parser over stored rows."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    store = BarStore(tmp_path / "bars.sqlite")
    ca.store_raw(store, "ACME", "actions('ACME',)",
                 {"data": [{"date": "01-Aug-2025", "purpose": "Split 1:2", "ratio": "0.5"}]})
    store.close()
    from indtrader.cli import main

    rc = main(["coractions", "ACME", "--from-raw"])
    out = capsys.readouterr().out
    get_settings.cache_clear()
    assert rc == 0 and "1 events parsed" in out and "1 split factors" in out
    # and a replay actually BUILDS the factor — that's the point of --from-raw
    s2 = BarStore(tmp_path / "bars.sqlite")
    sf = s2.split_factors("ACME")
    s2.close()
    assert list(sf.values) == [0.5]


def test_cli_coractions_needs_lib_for_live_probe(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    import indtrader.data.nseapi_provider as P
    monkeypatch.setattr(P.NseIndiaApiProvider, "available", staticmethod(lambda: False))
    from indtrader.cli import main

    assert main(["coractions", "RELIANCE"]) == 2
    assert "needs the nse lib" in capsys.readouterr().err
    get_settings.cache_clear()
