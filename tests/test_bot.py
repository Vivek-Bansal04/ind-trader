import json

import pytest

from indtrader.alerts import commands as C

CHAT = "12345"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    s = get_settings()
    from indtrader.config import Settings

    return Settings(**{**s.model_dump(), "telegram_bot_token": "tok", "telegram_chat_id": CHAT})


def upd(uid, chat, text):
    return {"update_id": uid, "message": {"chat": {"id": chat}, "text": text}}


# ---- the invariant that matters most ----------------------------------------

def test_registry_contains_no_order_verbs():
    banned = {"buy", "sell", "order", "execute", "place", "trade", "amend", "cancel", "square"}
    assert not banned & set(C.COMMANDS), f"order-ish command leaked into the bot: {banned & set(C.COMMANDS)}"
    # and the module must not import any broker adapter at all
    import pathlib
    import re

    src = pathlib.Path(C.__file__).read_text(encoding="utf-8")
    assert not re.search(r"from indtrader\.broker|import.*fyers", src, re.I)


# ---- auth + parsing -----------------------------------------------------------

def test_wrong_chat_is_dropped_before_dispatch(env):
    seen = []
    nxt = C.process_once(1, env, fetch=lambda tok, off: (2, [upd(1, 999, "/fill TATA BUY 1 10")]),
                         reply=seen.append)
    assert seen == [] and nxt == 2  # no reply, no execution, no signal back


def test_auth_requires_configured_chat(env):
    assert C.check_auth(CHAT, env) and not C.check_auth("999", env)
    from indtrader.config import Settings

    assert not C.check_auth(CHAT, Settings(telegram_chat_id=""))  # unconfigured bot accepts nothing


def test_parse_update_and_cmd():
    u = C.parse_update(upd(7, 5, "/status"))
    assert (u.update_id, u.chat_id, u.text) == (7, "5", "/status")
    assert C.parse_update({"junk": 1}) is None
    assert C.parse_cmd("/Fill TATA BUY 10 100") == ("fill", ["TATA", "BUY", "10", "100"])
    assert C.parse_cmd("") == ("", [])


def test_dispatch_never_raises_and_flags_unknown(env):
    assert "nothing was executed" in C.run_command("/frobnicate", env)
    assert "failed" in C.run_command("/book", env) or "Book" in C.run_command("/book", env)  # empty store ok


# ---- commands over real stores --------------------------------------------------

def test_fill_records_and_validates(env):
    assert "usage" in C.run_command("/fill TATA BUY 10", env)
    assert "BUY or SELL" in C.run_command("/fill TATA HOLD 10 100 0", env)
    assert "numbers" in C.run_command("/fill TATA BUY ten 100 0", env)
    assert "positive" in C.run_command("/fill TATA BUY 0 100 0", env)
    out = C.run_command("/fill tata buy 10 100.5 2", env)
    assert "recorded fill" in out and "TATA" in out.upper()

    from indtrader.execution.planlog import PlanLog

    log = PlanLog()
    fills = log.fills()
    note = log.con.execute("SELECT note FROM ledger ORDER BY id DESC LIMIT 1").fetchone()[0]
    log.close()
    assert len(fills) == 1 and fills[0].qty == 10 and fills[0].price == 100.5
    assert note == "via telegram bot"


def test_pause_resume_roundtrip_and_notify_gate(env):
    from indtrader.alerts.pause import is_paused

    assert "paused" in C.run_command("/pause", env) and is_paused(env)
    assert "resumed" in C.run_command("/resume", env) and not is_paused(env)

    # paused -> maybe_notify must refuse to send (and say why), unpause -> attempt
    from indtrader.alerts.briefing import maybe_notify

    C.run_command("/pause", env)
    import indtrader.alerts.briefing as br

    monkey_sent = []
    br_send = br.send
    br.send = lambda t, s: (monkey_sent.append(t), (True, "sent"))[1]
    try:
        sent, reason = maybe_notify(env)
        assert not sent and "PAUSED" in reason and monkey_sent == []
        C.run_command("/resume", env)
        sent, reason = maybe_notify(env)
        assert sent and len(monkey_sent) == 1
    finally:
        br.send = br_send


def test_status_and_book_on_empty_world_are_honest(env):
    assert "no fills" in C.run_command("/book", env)
    st = C.run_command("/status", env)
    assert "ETL" in st or "missing" in st  # never a crash, never a fake


def test_scan_reads_journaled_picks(env):
    from indtrader.execution.planlog import PlanLog

    log = PlanLog()
    log.record_signal("UNIVERSE", "scan", {"regime": "ON", "exposure_scale": 1,
                                           "picks": [{"symbol": "TATA", "score": 3, "quadrant": "LEAD",
                                                      "note": "results in 3d: half size"}]})
    log.close()
    out = C.run_command("/scan", env)
    assert "TATA" in out and "LEAD" in out and "half size" in out


def test_events_and_ann_empty_ok(env):
    assert "none stored" in C.run_command("/events", env)
    assert "all-clear" in C.run_command("/ann", env)


# ---- poll-loop plumbing -----------------------------------------------------------

def test_process_once_replies_authenticated_and_advances_offset(env):
    replies = []
    nxt = C.process_once(10, env,
                         fetch=lambda tok, off: (13, [upd(11, 555, "/status"),
                                                      upd(12, int(CHAT), "/book"),
                                                      upd(12, int(CHAT), "not-a-command")]),
                         reply=replies.append)
    assert nxt == 13
    assert len(replies) == 2  # wrong chat dropped; plain text answered as unknown-command, not ignored
    assert "unknown command" in replies[1]


def test_get_updates_network_failure_is_bounded(monkeypatch):
    def boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(C.httpx, "get", boom)
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    assert C.get_updates("tok", 5) == (5, [])  # returns, never raises, offset preserved
