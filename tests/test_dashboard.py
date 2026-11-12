import json
import threading
import time
from datetime import date
from urllib.request import urlopen

import pandas as pd
import pytest

from indtrader.data.store import BarStore
from indtrader.execution.planlog import PlanLog
from indtrader.models import OrderPlan
from indtrader.web.dashboard import make_handler, snapshot


def _last_settled():
    """Latest weekday strictly before today — fixtures anchor HERE so the suite
    stops date-rotting (a fixture ending on a hardcoded date turns the honest
    STALE banner into a false test failure days later)."""
    from datetime import date, timedelta

    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    store = BarStore(tmp_path / "bars.sqlite")
    log = PlanLog(tmp_path / "plans.sqlite")
    days = pd.bdate_range(end=_last_settled(), periods=25, freq="B")
    store.upsert_candles("NIFTY", "daily",
                         pd.DataFrame({"open": 25000, "high": 25100, "low": 24900,
                                       "close": range(25000, 25025), "volume": 1e6}, index=days), "test")
    store.upsert_candles("TATA", "daily",
                         pd.DataFrame({"open": 200, "high": 210, "low": 195,
                                       "close": 205.5, "volume": 1e5}, index=days), "test")
    log.add_plan(OrderPlan(symbol="TATA", action="BUY", qty=100, entry=200.0,
                           stop=180.0, target=240.0, reason="trend", setup="breakout"))
    log.add_fill(date(2026, 10, 6), "TATA", "BUY", 100, 200.0, 10.0, "sip-note")
    log.record_signal("UNIVERSE", "scan", {"regime": "NEUTRAL", "exposure_scale": 0.6,
                                           "picks": [{"symbol": "TATA", "score": 0.8,
                                                      "quadrant": "Leading", "note": "momo"}]})
    yield store, log
    store.close()
    log.close()
    get_settings.cache_clear()


def test_snapshot_composes_without_inventing(seeded):
    store, log = seeded
    log.con.execute("UPDATE plans SET status='executed' WHERE symbol='TATA'")
    log.con.commit()
    snap = snapshot(store, log, ["TATA"])
    assert "NIFTY 25,024" in snap["banner"] and "STALE" not in snap["banner"]
    assert snap["book"][0][0] == "TATA" and snap["book"][0][2] == 205.5     # marked from store
    assert snap["book"][0][4].startswith("<svg")                             # server-side sparkline
    assert snap["regime"]["label"] == "NEUTRAL"
    assert snap["shortlist"][0][0] == "TATA" and snap["shortlist"][0][2] == 0.8
    assert snap["shortlist"][0][4] == "momo" and snap["shortlist"][0][3].startswith("<svg")
    assert snap["problems"] == []                                           # fill traces to executed plan
    assert snap["health"]["problem"] == 0


def test_snapshot_flags_orphan_fills(seeded):
    store, log = seeded
    log.add_fill(date(2026, 10, 6), "GHOST", "BUY", 10, 100.0, 0.0)         # no plan behind it
    snap = snapshot(store, log, ["TATA"])
    assert any("GHOST" in p for p in snap["problems"])


def test_http_roundtrip(seeded):
    from http.server import ThreadingHTTPServer

    store, log = seeded
    srv = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(lambda: BarStore(store.path), lambda: PlanLog(log.path), ["TATA"]))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        page = None
        for _ in range(50):  # server is up as soon as it accepts; retry briefly
            try:
                page = urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
                break
            except OSError:
                time.sleep(0.1)
        assert page and "ind-trader" in page and "/api/status" in page
        data = json.loads(urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5).read())
        assert data["book"][0][0] == "TATA", data
    finally:
        srv.shutdown()
        srv.server_close()


# ---- ledger sections + honest absence -------------------------------------------

def test_ledger_sections_and_heatmap(seeded):
    store, log = seeded
    import numpy as np

    # anchor to the live calendar: CLIFFD's tail sits TWO sessions behind the
    # fixture's last NIFTY session, so the heat grid must name those holes
    # (the old hardcoded 2026-10 anchor date-rotted within a week).
    end2 = pd.bdate_range(end=pd.Timestamp(_last_settled()), periods=3)[0]
    days = pd.bdate_range(end=end2, periods=25)
    closes = np.array([100.0] * 12 + [50.0] * 13)          # −50% cliff, no factor -> gap
    store.upsert_candles("CLIFFD", "daily",
                         pd.DataFrame({"open": closes, "high": closes, "low": closes,
                                       "close": closes, "volume": 1e5}, index=days), "test")
    from indtrader.research.coractions import upsert_corporate_actions

    upsert_corporate_actions(store, "CLIFFD", [{"date": "2026-10-06", "kind": "Bonus Issue"}])   # unresolved
    upsert_corporate_actions(store, "CLIFFD", [{"date": "2026-10-07", "kind": "Dividend Rs 12"}])  # complete as-is
    from indtrader.data.macrocal import ensure as cal_ensure

    cal_ensure(store)
    store.con.execute("INSERT INTO calendar_events VALUES (?,'master',0,'fake drift','{}','now')",
                      [_last_settled()])
    store.con.execute("INSERT INTO calendar_events VALUES ('2099-01-05','master',0,'real holiday','{}','now')")
    store.con.commit()
    from indtrader.data.news import store_news

    store_news(store, "MARKET", [{"title": "TCS downgrade: margin outlook cut",
                                  "link": "http://x/1", "pubDate": ""}], "test")
    store_news(store, "MARKET", [{"title": "Market ends flat on quiet session",
                                  "link": "http://x/2", "pubDate": ""}], "test")
    snap = snapshot(store, log, ["TATA", "CLIFFD"])
    assert [r[0] for r in snap["split_gaps"]] == ["CLIFFD"] and "-50%" in snap["split_gaps"][0][1]
    assert snap["unresolved_ca"] == [["CLIFFD", "2026-10-06", "Bonus Issue"]]   # dividend NOT flagged
    assert _last_settled() in snap["holidays"]["disputed"]                       # holiday WITH a NIFTY bar
    assert "2099-01-05" not in snap["holidays"]["disputed"]                      # no bar = genuine, stays out
    assert snap["news"] and "downgrade" in snap["news"][0][2].lower()            # AMBER triage applied
    assert all("flat" not in r[2] for r in snap["news"])                         # quiet headline not flagged
    heat = {r[0]: r[1] for r in snap["heat"]["rows"]}
    assert heat["TATA"] and all(v == "fresh" for v in heat["TATA"].values())
    # CLIFFD's tail sits 2 sessions behind the calendar: the newest sessions must
    # read "missing" there — the grid's whole purpose is naming holes, not all-green.
    assert heat["CLIFFD"][_last_settled()] == "missing"
    assert heat["CLIFFD"][str(end2.date())] == "fresh"
    assert any(r[0] == "clean-day streak" and "not persisted" in r[1] for r in snap["etl"])


def test_honest_absences(seeded):
    store, log = seeded
    snap = snapshot(store, log, [])          # empty universe -> health error wording intact
    assert snap["health"]["error"]
    snap = snapshot(store, log, ["TATA"])
    # no news table at all -> "never fetched", NOT "quiet":
    assert snap["news"] == [] and "not fetched" in snap["news_note"]
    assert snap["unresolved_ca"] == [] and snap["split_gaps"] == []
    assert snap["holidays"]["coverage"] is None
    assert "not fetched" in snap["holidays"]["master_missing"]


def test_sparkline_svg_rules():
    from indtrader.web.dashboard import sparkline

    s = sparkline([1, 2, 3, 4, 5])
    assert s.startswith("<svg") and "polyline" in s and "#1a7f37" in s     # rising = green
    assert "#b3261e" in sparkline([5, 4, 3, 2, 1])                          # falling = red
    assert "stroke-dasharray" in sparkline([3, 3, 3])                        # flat = dash, no lie
    assert "stroke-dasharray" in sparkline([]) and "stroke-dasharray" in sparkline([None, "x"])


def test_serve_cleans_up_on_ctrl_c(tmp_path, monkeypatch):
    """Ctrl-C must exit 0: the per-request-connection refactor once left dead
    store/log closes in serve()'s finally — a NameError on every shutdown."""
    import threading

    import indtrader.web.dashboard as wb
    from indtrader.config import get_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()

    class FakeSrv:
        def __init__(self, *a):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr("http.server.ThreadingHTTPServer", FakeSrv)
    assert wb.serve(port=0) == 0
    get_settings.cache_clear()
