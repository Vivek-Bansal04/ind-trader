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
    assert snap["book"][0][4]["svg"].startswith("<svg")                     # server-side sparkline
    assert snap["regime"]["label"] == "NEUTRAL"
    # shortlist columns are symbol/score/quadrant/shape/note — the old "close"
    # column printed "Improving" because scan payloads have no close key at all
    # (laptop screenshot bug); score is rounded, never a 16-digit float.
    assert snap["shortlist"][0][0] == "TATA" and snap["shortlist"][0][1] == 0.8
    assert snap["shortlist"][0][2] == "Leading"
    assert snap["shortlist"][0][4] == "momo"
    assert snap["shortlist"][0][3]["svg"].startswith("<svg")    # spark cell is {svg:...}
    assert "not stored" in " ".join(snap["shortlist"][0][5]["lines"])  # old payload = honest gaps
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


# ---- the plain-words layer (redesign 2026-11-12): verdict + moves are PYTHON's job

def test_verdict_and_moves_words(seeded):
    store, log = seeded
    snap = snapshot(store, log, ["TATA"])
    assert snap["verdict"]["level"] == "green"          # bars fresh, NEUTRAL regime recorded
    assert "NEUTRAL" in snap["verdict"]["text"] and "60%" in snap["verdict"]["text"]
    titles = [m["title"] for m in snap["moves"]]
    assert any("waiting at your broker" in t for t in titles)
    card = next(m for m in snap["moves"] if "waiting at your broker" in m["title"])
    assert "Plan #" in card["body"] and "--plan" in card["after"]
    assert "TATA" in card["body"] and "delivery/CNC" in card["body"]
    assert "never places orders" in snap["rule"]
    # close the plan -> the card flips to the evening-flow card, never silence
    log.con.execute("UPDATE plans SET status='executed'")
    log.con.commit()
    snap2 = snapshot(store, log, ["TATA"])
    assert any("Nothing waiting" in m["title"] for m in snap2["moves"])


def test_verdict_stops_on_index_staleness(seeded):
    """Data truth outranks the market light: advice measured on old bars is not
    advice, and the page must SAY stop before it says anything else. (The
    fixture's TATA misses the newest session too — since the 2026-11-12
    threshold rule a missing HOLDING is independently red; this test pins the
    INDEX-staleness wording, which fires first.)"""
    store, log = seeded

    old = pd.bdate_range(end="2026-08-03", periods=30)
    store.con.execute("DELETE FROM candles WHERE symbol='NIFTY'")
    store.upsert_candles("NIFTY", "daily",
                         pd.DataFrame({"open": 25000, "high": 25100, "low": 24900,
                                       "close": 25000, "volume": 1e6}, index=old), "test")
    snap = snapshot(store, log, ["TATA"])
    assert snap["verdict"]["level"] == "red" and "STOP" in snap["verdict"]["text"]


def test_moves_name_the_human_workload(seeded):
    """UNRESOLVED actions must surface as a card WITH the exact --set command —
    the layman page is also the to-do list, and 'unknown' without a next step
    is just anxiety."""
    store, log = seeded
    from indtrader.research.coractions import upsert_corporate_actions

    upsert_corporate_actions(store, "RELIANCE",
                             [{"date": "07-Sep-2017", "kind": "Bonus Issue", "ratio": None}])
    snap = snapshot(store, log, ["TATA"])
    row = next(r for r in snap["unresolved_ca"] if r[0] == "RELIANCE")
    assert row[1] == "2017-09-07"              # ISO-normal at storage (was '07-Sep-2017',
                                               # then truncated on screen to '07-Sep-201')
    card = next(m for m in snap["moves"] if "price history" in m["title"])
    assert "indtrader coractions RELIANCE --set '2017-09-07|" in card["after"]
    assert card["state"] == "amber"


def test_score_is_displayed_rounded(seeded):
    """2.851851851851852 on a human page is a bug, not precision (laptop screenshot)."""
    store, log = seeded
    log.record_signal("UNIVERSE", "scan", {"regime": "ON", "exposure_scale": 1.0,
                                           "picks": [{"symbol": "TATA", "score": 2.851851851851852,
                                                      "quadrant": "Leading"}]})
    snap = snapshot(store, log, ["TATA"])
    assert snap["shortlist"][0][1] == 2.85


def test_shortlist_why_decomposes_the_score(seeded):
    """The row 'why' explains the score with the scanner's OWN arithmetic —
    quadrant points + momentum rank + bulk boost; never an invented number."""
    store, log = seeded
    log.record_signal("UNIVERSE", "scan", {
        "regime": "ON", "exposure_scale": 1.0,
        "picks": [{"symbol": "TATA", "score": 2.85, "quadrant": "Leading", "mom_6m": 0.18,
                   "deliv_per": 62.0, "deliv_avg20": 55.0, "net_bulk": 2.5e7,
                   "turnover_20d": 8.4e8, "base_quality": "tight", "note": "compressed base"}]})
    snap = snapshot(store, log, ["TATA"])
    why = snap["shortlist"][0][5]
    joined = " | ".join(why["lines"])
    assert "+1.5 points" in joined                       # Leading = 1.5 (scanner's table)
    assert "+18.0%" in joined                            # mom_6m in human form
    assert "buying ₹25,000,000" in joined and "+0.5 added" in joined
    assert "= +1.5 quadrant + 0.85 momentum rank + 0.50 bulk buying → score 2.85" in joined
    tail = why["tail"]
    assert "delivery on the latest session 62% vs 20-day average 55%" in tail
    assert "84.0 crore/day" in tail and "tight" in tail and "compressed base" in tail


def test_shortlist_why_says_not_stored_never_zero(seeded):
    """An OLD scan payload (pre-evidence-keys) must render 'not stored' — unknown
    is not zero, even in a tooltip."""
    store, log = seeded
    snap = snapshot(store, log, ["TATA"])       # fixture payload has only symbol/score/quadrant/note
    why = snap["shortlist"][0][5]
    joined = " | ".join(why["lines"])
    assert "not stored" in joined and "Bulk-deal feed: unknown" in joined
    assert "turnover unknown" in why["tail"]
    assert "delivery feed unknown" in why["tail"]


def test_quadrant_points_pinned_to_scanner():
    """dashboard.QUADRANT_POINTS re-states scanner.QUADRANT_SCORE for display —
    if they ever drift, the page's arithmetic lies, so THIS test is the drift wire."""
    from indtrader.research.scanner import QUADRANT_SCORE
    from indtrader.web.dashboard import QUADRANT_POINTS

    assert QUADRANT_POINTS == QUADRANT_SCORE


def test_page_source_carries_no_python_escaped_quotes():
    """PAGE is one Python string: a \\' inside it un-escapes to a bare ' before the
    browser sees it, breaking the whole JS (a broken JS string = permanent
    'loading…', observed 2026-11-12). Apostrophes belong in double-quoted JS
    strings or HTML entities — the Python escape is the trap."""
    import pathlib
    import re

    import indtrader.web.dashboard as wb

    src = pathlib.Path(wb.__file__).read_text(encoding="utf-8")
    page_src = src.split('PAGE = """')[1].split('"""')[0]
    assert "\\'" not in page_src, 'use "double-quoted" JS strings or &apos; — never \\\' inside PAGE'
    # crude balance tripwire (counts parens inside string TEXT too — fine while the
    # copy stays balanced): a dropped-paren edit like the 2026-11-12 one trips here.
    js = re.search(r"(?s)<script>(.*?)</script>", page_src).group(1)
    assert js.count("(") == js.count(")")
    assert js.count("{") == js.count("}")
    assert js.count("[") == js.count("]")


def test_serve_prefers_universe_file(tmp_path, monkeypatch):
    """serve() must read data/universe.txt like review/riskreport do — the old
    load_universe(None) showed 10 symbols on a machine holding 500."""
    import indtrader.web.dashboard as wb
    from indtrader.config import get_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    (tmp_path / "universe.txt").write_text("AAA\nBBB\tEnergy\n# comment\nCCC\n", encoding="utf-8")
    seen: dict = {}

    def fake_make(store_factory, log_factory, uni):
        seen["universe"] = list(uni)
        return type("H", (), {})

    class FakeSrv:
        def __init__(self, *a):
            pass

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    monkeypatch.setattr(wb, "make_handler", fake_make)
    monkeypatch.setattr("http.server.ThreadingHTTPServer", FakeSrv)
    assert wb.serve(port=0) == 0
    assert seen["universe"] == ["AAA", "BBB", "CCC"]      # the 3-line file, comments dropped
    other = tmp_path / "other.txt"
    other.write_text("ZZZ\n", encoding="utf-8")
    seen.clear()
    assert wb.serve(port=0, universe_file=str(other)) == 0
    assert seen["universe"] == ["ZZZ"]                    # --universe beats the default
    seen.clear()
    (tmp_path / "universe.txt").unlink()
    assert wb.serve(port=0) == 0
    assert len(seen["universe"]) == 10                    # labeled built-in default fallback
    get_settings.cache_clear()


# ---- verdict thresholds (operator rule 2026-11-12): whose money does a miss blind?

def _stale_one(store, sym, n=25):
    """Re-seed `sym`'s bars ending ~1 week before the fixture's index calendar."""
    old = pd.bdate_range(end=pd.Timestamp(_last_settled()) - pd.offsets.BDay(5), periods=n)
    store.con.execute("DELETE FROM candles WHERE symbol=?", [sym])
    store.upsert_candles(sym, "daily",
                         pd.DataFrame({"open": 100, "high": 101, "low": 99,
                                       "close": 100, "volume": 1e5}, index=old), "test")


def test_few_universe_misses_are_amber_not_red(seeded):
    """One delisted junk name out of hundreds must NOT turn the page STOP — the
    always-red warning is the round-6 'EMPTY: 499 symbols' failure reborn."""
    store, log = seeded
    # 40 healthy names + 1 (GONE) whose bars stop 3 sessions early = 1/41 ≈ 2.4%
    uni = ["GONE"] + [f"OK{i}" for i in range(1, 41)]
    store.upsert_candles("GONE", "daily",
                         pd.DataFrame({"open": 50, "high": 51, "low": 49, "close": 50,
                                       "volume": 1e5},
                                      index=pd.bdate_range(end=_last_settled(), periods=25)
                                      - pd.offsets.BDay(3)), "test")
    for i in range(1, 41):
        store.upsert_candles(f"OK{i}", "daily",
                             pd.DataFrame({"open": 50, "high": 51, "low": 49, "close": 50,
                                           "volume": 1e5},
                                          index=pd.bdate_range(end=_last_settled(), periods=25)), "test")
    snap = snapshot(store, log, uni)
    assert len(snap["health"]["latest_missing"]) == 1        # only GONE
    assert snap["verdict"]["level"] != "red", snap["verdict"]
    assert any("no bar for the newest" in m["title"] and "GONE" in m["body"]
               for m in snap["moves"]), [m["title"] for m in snap["moves"]]


def test_widespread_universe_misses_are_red(seeded):
    """> 10% of the universe missing = the FETCH is failing — page-wide STOP."""
    store, log = seeded
    uni = []
    for i in range(10):
        s = f"BAD{i}"
        uni.append(s)
        store.upsert_candles(s, "daily",
                             pd.DataFrame({"open": 50, "high": 51, "low": 49, "close": 50,
                                           "volume": 1e5},
                                          index=pd.bdate_range(end=_last_settled(), periods=25)
                                          - pd.offsets.BDay(3)), "test")
    snap = snapshot(store, log, uni)
    assert len(snap["health"]["latest_missing"]) == 10
    assert snap["verdict"]["level"] == "red"
    assert "10 of 10" in snap["verdict"]["text"] and "fetch is failing" in snap["verdict"]["text"]


def test_missing_bar_on_a_holding_is_red_even_outside_the_universe(seeded):
    """A holding absent from universe.txt cannot hide behind the 10% rule —
    the check reads the bars themselves, and YOUR name missing is always loud."""
    store, log = seeded                      # fixture holds TATA (fill, no executed plan)
    _stale_one(store, "TATA")
    snap = snapshot(store, log, [])          # empty universe: heatmap never sees TATA
    assert snap["verdict"]["level"] == "red"
    assert "TATA" in snap["verdict"]["text"] and "unmeasured" in snap["verdict"]["text"]


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
