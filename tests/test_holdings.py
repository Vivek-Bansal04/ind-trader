"""The holdings feature: journal write path, FIFO view, good-news judgement and
the deduped Telegram watchers. All offline — Telegram is monkeypatched, the
flag file is monkeypatched, no network anywhere."""
import json
import threading
import time
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.request import urlopen

import pandas as pd
import pytest

from indtrader.alerts import holdings
from indtrader.data.news import store_news
from indtrader.data.store import BarStore
from indtrader.execution.planlog import PlanLog


def _last_settled():
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
    yield store, log
    store.close()
    log.close()
    get_settings.cache_clear()


# ---- record_holding: the ONLY thing the dashboard form can do -------------------

def test_record_holding_lands_a_normal_fill(seeded):
    store, log = seeded
    fid = holdings.record_holding(log, "tata", "100", "200.50", costs="12.5",
                                  note="from my broker note")
    row = log.con.execute("SELECT dt,symbol,side,qty,price,costs,note FROM ledger "
                          "WHERE id=?", [fid]).fetchone()
    assert row[1] == "TATA" and row[2] == "BUY"           # normalized, uppercase side
    assert row[3] == 100 and row[4] == 200.50 and row[5] == 12.5
    assert "from my broker note" in row[6] and holdings.DASHBOARD_NOTE in row[6]
    # it shows up as held immediately — same ledger the whole system reads
    from indtrader.portfolio.valuation import held_quantities

    assert held_quantities(log.fills())["TATA"] == 100


def test_record_holding_refuses_every_malformed_input(seeded):
    store, log = seeded
    bad = [
        (dict(symbol="TATA POS", qty=1, price=1), "symbol"),      # spaces, prose
        (dict(symbol="", qty=1, price=1), "symbol"),
        (dict(symbol="TATA", qty=0, price=1), "whole number"),
        (dict(symbol="TATA", qty="2.5", price=1), "whole number"),  # fractional = refused, not truncated
        (dict(symbol="TATA", qty=-10, price=1), "whole number"),
        (dict(symbol="TATA", qty=1, price=0), "positive"),
        (dict(symbol="TATA", qty=1, price="abc"), "number"),
        (dict(symbol="TATA", qty=1, price=1, costs=-1), "negative"),
        (dict(symbol="TATA", qty=1, price=1, dt="next tuesday"), "YYYY-MM-DD"),  # '07-Sep-2026' IS accepted (parse-or-refuse)
    ]
    for kwargs, phrase in bad:
        with pytest.raises(ValueError) as e:
            holdings.record_holding(log, kwargs["symbol"], kwargs["qty"], kwargs["price"],
                                    dt=kwargs.get("dt"), costs=kwargs.get("costs", 0.0))
        assert phrase in str(e.value), (kwargs, str(e.value))
    assert log.con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0  # nothing slipped in


def test_record_holding_empty_date_means_today(seeded):
    store, log = seeded
    holdings.record_holding(log, "TATA", 10, 100)
    dt = log.con.execute("SELECT dt FROM ledger").fetchone()[0]
    assert str(dt)[:10] == date.today().isoformat()


# ---- holdings_view: FIFO basis, honest unknowns ---------------------------------

def test_holdings_view_fifo_basis_after_partial_sell(seeded):
    store, log = seeded
    holdings.record_holding(log, "TATA", 100, 200.0,
                            dt=(date.today() - timedelta(days=5)).isoformat())
    log.add_fill(date.today() - timedelta(days=2), "TATA", "SELL", 100, 250.0)
    holdings.record_holding(log, "TATA", 100, 300.0)
    v = holdings.holdings_view(store, log)
    r = v["rows"][0]
    assert r["qty"] == 100
    assert r["avg"] == 300.0            # FIFO consumed the 200 lot — basis of what is LEFT
    assert r["close"] == 205.5
    assert r["pnl"] == -9450.0          # 100 * (205.5 - 300)
    assert r["spark"]["svg"].startswith("<svg")
    assert "date unknown" in r["results"]      # no events row => unknown, never "far away"
    assert r["news"] == []


def test_holdings_view_results_countdown_and_news(seeded):
    store, log = seeded
    holdings.record_holding(log, "TATA", 10, 100.0)
    from indtrader.research import events

    soon = (date.today() + timedelta(days=3)).isoformat()
    events.store_events(store, [{"symbol": "TATA", "d": soon, "kind": "results"}])
    store_news(store, "TATA", [{"title": "Tata fraud probe opens at vendor",
                                "link": "http://x/9", "pubDate": ""}], "test")
    r = holdings.holdings_view(store, log)["rows"][0]
    assert soon in r["results"] and "in 3 days" in r["results"]
    assert r["news"] and r["news"][0][1].startswith("[RED]")   # triage label rides along


def test_holdings_view_empty_book_is_a_hint_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    store, log = BarStore(tmp_path / "b.sqlite"), PlanLog(tmp_path / "p.sqlite")
    v = holdings.holdings_view(store, log)
    assert v["empty_ok"] and "same journal" in v["hint"].lower()
    store.close()
    log.close()
    get_settings.cache_clear()


# ---- good news: GREEN only when not ALSO a warning -------------------------------

def test_green_title_matches_words_not_noise():
    assert holdings.green_title("Infy posts record profit, best quarter yet")
    assert holdings.green_title("buyback of 5,000 crore approved") is not None
    assert holdings.green_title("Recruitment drive launches for 3,000 staff") is None  # 'record'≠'record high'


def test_good_news_ships_clean_good_and_vetoes_both_ways(seeded):
    store, log = seeded
    store_news(store, "GOOD", [{"title": "GoodCo bags order worth 4,000 crore",
                                "link": "http://x/1", "pubDate": ""}], "test")
    store_news(store, "BOTH", [{"title": "BothCo profit jumps 40% as fraud probe opens",
                                "link": "http://x/2", "pubDate": ""}], "test")
    store_news(store, "WARN", [{"title": "WarnCo board meeting to consider results",
                                "link": "http://x/3", "pubDate": ""}], "test")
    store_news(store, "PLAIN", [{"title": "PlainCo shares trade flat on Dalal Street",
                                 "link": "http://x/4", "pubDate": ""}], "test")
    got = {g["symbol"] for g in holdings.good_news(store, ["GOOD", "BOTH", "WARN", "PLAIN"])}
    assert got == {"GOOD"}   # both-ways + AMBER-only stay warnings; plain stays out


# ---- watchers: countdown text, once-only send, honest failure modes --------------

class FakeSet:
    """Config surface of send_holdings_alerts without env archaeology."""

    def __init__(self, data_dir, configured=True):
        self.data_dir = data_dir
        self.telegram_bot_token = "t" if configured else ""
        self.telegram_chat_id = "c" if configured else ""


@pytest.fixture
def send_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    sent: list[tuple[str, str]] = []

    def fake_send(text, s):
        sent.append((text, "ok"))
        return True, "ok"

    # send_holdings_alerts resolves send() at call time → this patch is what runs
    monkeypatch.setattr("indtrader.alerts.telegram.send", fake_send)
    yield sent
    get_settings.cache_clear()


def _due_store_log(tmp_path):
    store, log = BarStore(tmp_path / "bars.sqlite"), PlanLog(tmp_path / "plans.sqlite")
    holdings.record_holding(log, "DUE", 10, 100.0)
    from indtrader.research import events

    events.store_events(store, [{"symbol": "DUE",
                                 "d": (date.today() + timedelta(days=3)).isoformat(),
                                 "kind": "results"}])
    return store, log


def test_results_alert_sends_once_never_repeats(tmp_path, monkeypatch, send_env):
    store, log = _due_store_log(tmp_path)
    s = FakeSet(tmp_path)
    rep = holdings.send_holdings_alerts(store, log, s)
    assert rep["sent"] and len(rep["results_sent"]) == 1 and len(send_env) == 1
    assert "results in 3 days" in send_env[0][0]
    rep2 = holdings.send_holdings_alerts(store, log, s)      # same evening, second run
    assert not rep2["sent"] and len(send_env) == 1           # dedup: never re-sends
    store.close()
    log.close()


def test_unknown_results_are_listed_not_silently_skipped(tmp_path, monkeypatch, send_env):
    store, log = _due_store_log(tmp_path)
    holdings.record_holding(log, "NODATE", 5, 50.0)          # held, no calendar row
    rep = holdings.send_holdings_alerts(store, log, FakeSet(tmp_path))
    assert rep["results_unknown"] == ["NODATE"]
    store.close()
    log.close()


def test_paused_loop_holds_alerts_back(tmp_path, monkeypatch, send_env):
    store, log = _due_store_log(tmp_path)
    (tmp_path / "paused.flag").write_text("paused", encoding="utf-8")
    rep = holdings.send_holdings_alerts(store, log, FakeSet(tmp_path))
    assert rep["skipped_pause"] and not rep["sent"] and not send_env
    store.close()
    log.close()


def test_unconfigured_telegram_is_stated_nothing_marked(tmp_path, monkeypatch, send_env):
    store, log = _due_store_log(tmp_path)
    rep = holdings.send_holdings_alerts(store, log, FakeSet(tmp_path, configured=False))
    assert not rep["sent"] and "not configured" in rep["reason"] and not send_env
    # and NOTHING was marked sent — a re-run after configuring must still fire:
    rep2 = holdings.send_holdings_alerts(store, log, FakeSet(tmp_path))
    assert rep2["sent"] and len(send_env) == 1
    store.close()
    log.close()


def test_failed_send_leaves_the_alert_due(tmp_path, monkeypatch, send_env):
    store, log = _due_store_log(tmp_path)

    def fail_send(text, s):
        return False, "telegram unreachable"

    monkeypatch.setattr("indtrader.alerts.telegram.send", fail_send)
    rep = holdings.send_holdings_alerts(store, log, FakeSet(tmp_path))
    assert not rep["sent"] and "send failed" in rep["reason"]
    retry: list[str] = []

    def ok_send(text, s):
        retry.append(text)
        return True, "ok"

    monkeypatch.setattr("indtrader.alerts.telegram.send", ok_send)
    rep2 = holdings.send_holdings_alerts(store, log, FakeSet(tmp_path))
    assert rep2["sent"] and len(retry) == 1                # re-fires tomorrow, as promised
    store.close()
    log.close()


def test_mf_holdings_are_never_telegram_targets(tmp_path, monkeypatch, send_env):
    store, log = BarStore(tmp_path / "b.sqlite"), PlanLog(tmp_path / "p.sqlite")
    log.add_fill(date.today(), "MF:PARAG", "BUY", 10.5, 50.0)   # fractional units
    rep = holdings.send_holdings_alerts(store, log, FakeSet(tmp_path))
    assert rep["held"] == 0 and "no holdings" in rep["reason"]
    store.close()
    log.close()


# ---- the dashboard write path (POST /api/journal) --------------------------------

def _post(port, payload):
    req = urlopen(f"http://127.0.0.1:{port}/api/journal",  # noqa: S310 (test server)
                  data=json.dumps(payload).encode(), timeout=5)
    return req.status, json.loads(req.read())


def _post_err(port, payload):
    try:
        urlopen(f"http://127.0.0.1:{port}/api/journal", data=json.dumps(payload).encode(), timeout=5)
        return None
    except HTTPError as e:
        return e.code, json.loads(e.read())


def test_dashboard_post_records_and_refuses(seeded):
    from http.server import ThreadingHTTPServer

    from indtrader.web.dashboard import make_handler

    store, log = seeded
    srv = ThreadingHTTPServer(("127.0.0.1", 0),
                              make_handler(lambda: BarStore(store.path),
                                           lambda: PlanLog(log.path), ["TATA"]))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        code, body = _post(port, {"symbol": "INFY", "qty": "10", "price": "1450.5",
                                  "dt": "2026-11-02", "note": "manual entry"})
        assert code == 200 and body["ok"] and isinstance(body["id"], int)
        row = log.con.execute("SELECT symbol,qty,price,dt FROM ledger WHERE id=?",
                              [body["id"]]).fetchone()
        assert row == ("INFY", 10.0, 1450.5, "2026-11-02")
        # refusal: malformed qty -> 400 with the validation's own words
        err = _post_err(port, {"symbol": "INFY", "qty": "2.5", "price": "1"})
        assert err and err[0] == 400 and "whole number" in err[1]["error"]
        # the snapshot now shows the fill on the holdings panel
        data = json.loads(urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5).read())
        assert {r["symbol"] for r in data["holdings"]["rows"]} == {"INFY"}
        assert data["results_alert_days"] == holdings.RESULTS_ALERT_DAYS
    finally:
        srv.shutdown()
        srv.server_close()


def test_dashboard_post_unknown_path_is_404(seeded):
    from http.server import ThreadingHTTPServer

    from indtrader.web.dashboard import make_handler

    store, log = seeded
    srv = ThreadingHTTPServer(("127.0.0.1", 0),
                              make_handler(lambda: BarStore(store.path),
                                           lambda: PlanLog(log.path), ["TATA"]))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        import urllib.error

        try:
            urlopen(f"http://127.0.0.1:{port}/api/place-order", data=b"{}", timeout=5)
            pytest.fail("a non-journal POST path answered — there must be none")
        except HTTPError as e:
            assert e.code == 404
        except urllib.error.URLError:
            pytest.fail("connection dropped instead of a clean 404")
    finally:
        srv.shutdown()
        srv.server_close()
