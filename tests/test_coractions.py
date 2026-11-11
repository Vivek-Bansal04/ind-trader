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


# ---- the ROUND-6 CONFIRMED payload shape (PASTE 4 key dump) -----------------------------

def NSE_ROW(**kw):  # keys exactly as probed live on the laptop, 2026-11-11
    row = {"bcEndDate": "28-Aug-2025", "bcStartDate": "26-Aug-2025",
           "caBroadcastDate": "20-Aug-2025", "comp": "HDFC Bank Ltd",
           "exDate": "26-Aug-2025", "faceVal": 2.5, "ind": "BANK",
           "isin": "INE040A01034", "ndEndDate": "26-Aug-2025", "ndStartDate": "26-Aug-2025",
           "recDate": "26-Aug-2025", "series": "EQ", "subject": "Bonus",
           "symbol": "HDFCBANK"}
    row.update(kw)
    return row


def test_confirmed_shape_parses_and_ratioless_events_stay_unresolved(store):
    payload = {"data": [NSE_ROW(),
                        NSE_ROW(exDate="14-Jul-2011", subject="Split of shares from Rs 10 to Rs 2",
                                faceVal=2.0)]}
    events = ca.parse_events("HDFCBANK", payload)
    assert len(events) == 2
    assert events[0]["date"] == "26-Aug-2025" and events[0]["faceVal"] == 2.5
    n, unresolved = ca.build_split_factors(store, "HDFCBANK", events)
    # BOTH subjects LOOK like splits/bonuses but state no usable numbers:
    # factors NOT stored, both NAMED. The row's faceVal alone cannot give a
    # ratio — the payload never says the face value BEFORE the action — so a
    # ratio derived from it would be a guess, and guesses are what this repo
    # refuses. health keeps HDFCBANK on the split-gap list until real ratio
    # text (or a human --set) lands.
    assert n == 1 and unresolved == ["26-Aug-2025: Bonus"]
    # "from Rs 10 to Rs 2" states numbers -> price multiplier 0.2, ISO-normalised
    sf = store.split_factors("HDFCBANK")
    assert str(sf.index[0].date()) == "2011-07-14" and float(sf.iloc[0]) == 0.2
    note = ca.record_parsed(store, "HDFCBANK", events)
    assert "UNRESOLVED 1" in note                       # and the operator SEES it


def test_unparseable_date_raises_not_skips():
    with pytest.raises(ValueError, match="unparseable"):
        ca.parse_iso_day("whenever")
    assert ca.parse_iso_day("26-Aug-2025").isoformat() == "2025-08-26"


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
