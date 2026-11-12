import numpy as np
import pandas as pd
import pytest

from indtrader.data.store import BarStore
from indtrader.execution.planlog import PlanLog
from indtrader.research import events as ev
from indtrader.research.paperloop import draft_for, morning_brief


def _bars(n=300, seed=3, vol=0.008, drift=0.0006, end=None):
    rng = np.random.default_rng(seed)
    close = 200 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    # same convention as tests/test_events.py: TODAY sits INSIDE the calendar —
    # sessions_until() counts stored sessions, so a fixture whose bars stop at
    # today cannot measure distance to any future event.
    end = pd.Timestamp(end) if end is not None else pd.Timestamp.today().normalize() + pd.offsets.BDay(20)
    dates = pd.bdate_range(end=end, periods=n)
    return pd.DataFrame({"open": close, "high": close * 1.004, "low": close * 0.996,
                         "close": close, "volume": 2e6}, index=dates)


@pytest.fixture
def env(tmp_path):
    store = BarStore(tmp_path / "b.sqlite")
    log = PlanLog(tmp_path / "p.sqlite")
    end = pd.Timestamp.today().normalize() + pd.offsets.BDay(20)
    store.upsert_candles("NIFTY", "daily", _bars(end=end), "test")
    store.upsert_candles("GOOD", "daily", _bars(seed=9, end=end), "test")
    yield store, log
    store.close()
    log.close()


def test_draft_is_a_sized_proposal_with_evidence_in_the_reason(env):
    store, _ = env
    d = draft_for(store, "GOOD", equity=1_000_000, cash=2_000_000)
    assert d.skip == "" and d.qty > 0
    assert 0 < d.stop < d.entry < d.target          # would pass cmd_plan's front door
    assert len(d.reason) >= 10
    assert "200dma" in d.reason and "DRAFT for human review" in d.reason


def test_results_rule_reaches_the_draft_reason(env):
    store, _ = env
    d_far = draft_for(store, "GOOD", 1_000_000)
    nxt = pd.bdate_range(start=pd.Timestamp.today().normalize(), periods=4)[3]
    ev.store_events(store, [{"symbol": "GOOD", "d": nxt.date().isoformat(), "kind": "results"}])
    d = draft_for(store, "GOOD", 1_000_000)
    assert d.qty == max(1, d_far.qty // 2)
    assert "half size" in d.reason


def test_no_invented_qty_when_sizing_or_events_refuse(env):
    store, _ = env
    d = draft_for(store, "GHOST", 1_000_000)
    assert d.skip and d.qty == 0                     # unknown data = no draft, not qty 1
    nxt2 = pd.bdate_range(start=pd.Timestamp.today().normalize(), periods=3)[2]
    ev.store_events(store, [{"symbol": "GOOD", "d": nxt2.date().isoformat(), "kind": "results"}])
    d2 = draft_for(store, "GOOD", 1_000_000)
    assert "NO fresh entry" in d2.skip and d2.qty == 0


def test_morning_brief_reports_stops_expiry_and_exempts_nothing(env):
    store, log = env
    from indtrader.models import OrderPlan
    # one plan already under its stop, one healthy, one STALE-by-calendar symbol
    log.add_plan(OrderPlan("GOOD", "BUY", 10, 10_000.0, 9_000.0, 12_000.0,
                           "well above last close — gap up territory", "breakout"))
    close_last = float(store.load("GOOD")["close"].iloc[-1])
    log.add_plan(OrderPlan("GOOD", "BUY", 10, close_last, close_last * 0.95,
                           close_last * 1.1, "healthy plan: stop below last close", "manual"))
    log.add_plan(OrderPlan("GHOST", "BUY", 5, 100.0, 90.0, 120.0, "no bars in store anywhere",
                           "manual"))
    text = morning_brief(store, log)
    assert "already BELOW stop" in text             # stop-breached case named
    assert text.count("BELOW stop") == 1            # the healthy plan is NOT flagged
    assert "flat vs entry" in text                  # the healthy plan's own line
    assert "NO DATA" in text and "unknown is not safe" in text
    # a FRESH plan must not be TTL-expired by the brief itself; twice is safe
    assert morning_brief(store, log).count("morning brief") == 1


def test_brief_says_so_when_nothing_is_open(env):
    _, log = env
    assert "no open plans" in morning_brief(*env)
