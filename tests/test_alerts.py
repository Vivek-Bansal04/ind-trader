import numpy as np
import pandas as pd

from indtrader.alerts.briefing import format_briefing
from indtrader.alerts.telegram import inr, send
from indtrader.config import Settings
from indtrader.research.regime import Regime
from indtrader.research.scanner import scan


def test_inr_formats():
    assert inr(141_000_000) == "+14.1Cr"
    assert inr(-310_000) == "-3.1L"
    assert inr(9_500) == "+9,500"
    assert inr(0) == "0"


def test_send_not_configured():
    ok, reason = send("hi", Settings(telegram_bot_token="", telegram_chat_id=""))
    assert ok is False and "not configured" in reason


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = str(body)

    def json(self):
        return self._body


def test_send_ok(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return FakeResp(200, {"ok": True})

    monkeypatch.setattr("indtrader.alerts.telegram.httpx.post", fake_post)
    s = Settings(telegram_bot_token="tok", telegram_chat_id="123")
    ok, reason = send("hello", s)
    assert ok and reason == "sent"
    assert "bottok/sendMessage" in calls[0][0] and calls[0][1]["chat_id"] == "123"  # bot+token concatenate


def test_send_429_retries_once(monkeypatch):
    seq = [FakeResp(429, {"ok": False, "parameters": {"retry_after": 0}}), FakeResp(200, {"ok": True})]
    monkeypatch.setattr("indtrader.alerts.telegram.httpx.post", lambda *a, **k: seq.pop(0))
    monkeypatch.setattr("indtrader.alerts.telegram.time.sleep", lambda s: None)
    ok, _ = send("x", Settings(telegram_bot_token="t", telegram_chat_id="1"))
    assert ok


def test_send_500_never_raises(monkeypatch):
    monkeypatch.setattr("indtrader.alerts.telegram.httpx.post",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("dns down")))
    ok, reason = send("x", Settings(telegram_bot_token="t", telegram_chat_id="1"))
    assert ok is False and "RuntimeError" in reason


def _rising(end, n=300):
    return pd.Series(np.linspace(100.0, end, n), index=pd.bdate_range("2023-01-01", periods=n))


def _falling(n=300):
    return pd.Series(np.linspace(120.0, 100.0, n), index=pd.bdate_range("2023-01-01", periods=n))


def test_briefing_format_full():
    closes = {"WIN": _rising(170), "FLAT": _falling()}
    extras = {"WIN": {"deliv_per": 62.0, "deliv_avg": 55.0, "net_bulk": 141_000_000.0}}
    table = scan(closes, bench=_rising(120), regime=Regime("ON", 1.0, 0.66, True), extras=extras)
    text = format_briefing("2026-11-10", Regime("ON", 1.0, 0.66, True), table)
    assert "Regime ON | exposure 1" in text and "breadth 0.66" in text
    assert "WIN" in text and "+14.1Cr" in text and "deliv 62%" in text
    assert "FLAT" not in text.split("Shortlist:")[1]  # below trend gate, not listed
    assert "turns OFF if" in text                     # flip-level footer present


def test_briefing_format_empty_store():
    text = format_briefing("2026-11-10", None, None)
    assert "No shortlist" in text and "fetch-universe" in text
