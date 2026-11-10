from indtrader.ai.base import AIInsight
from indtrader.ai.router import NullProvider, Router, parse_insight


class FakeProvider:
    name = "fake"

    def __init__(self, avail=True, result=None, raises=False):
        self._avail, self._result, self._raises = avail, result, raises

    def available(self):
        return self._avail

    def analyze(self, prompt):
        if self._raises:
            raise RuntimeError("provider down")
        return self._result


def test_parse_valid():
    ins = parse_insight("x", 'Sure! {"verdict":"buy","confidence":72,"summary":"trend ok","risks":"vol"} thanks')
    assert ins.verdict == "BUY" and ins.confidence == 72


def test_parse_garbage_degrades():
    ins = parse_insight("x", "I cannot answer that")
    assert ins.verdict == "UNPARSEABLE" and ins.confidence == 0


def test_parse_bad_verdict_degrades():
    ins = parse_insight("x", '{"verdict":"YOLO","confidence":50,"summary":"","risks":""}')
    assert ins.verdict == "UNPARSEABLE"


def test_router_falls_back_on_failure():
    ok = AIInsight("second", "HOLD", 50, "s", "r")
    r = Router([FakeProvider(avails := True, raises=True), FakeProvider(True, ok), NullProvider()])
    out = r.analyze("p")
    assert out is ok
    assert FakeProvider(False, None).available() is False


def test_router_none_when_nothing_available():
    r = Router([FakeProvider(False)])
    assert r.analyze("p") is None


def test_null_provider_returns_none():
    assert NullProvider().analyze("p") is None
