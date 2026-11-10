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


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    store = BarStore(tmp_path / "bars.sqlite")
    log = PlanLog(tmp_path / "plans.sqlite")
    days = pd.date_range("2026-10-01", periods=25, freq="B")
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
    assert snap["regime"]["label"] == "NEUTRAL"
    assert snap["shortlist"] == [["TATA", "Leading", 0.8, "momo"]]
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
