import numpy as np
import pandas as pd

from indtrader.data.store import BarStore
from indtrader.research import events

TODAY = pd.Timestamp("2026-11-10")
IDX = pd.bdate_range("2026-01-01", periods=300)  # TODAY sits INSIDE the calendar


def setup(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")
    c = np.linspace(24000, 25000, len(IDX))
    s.upsert_candles("NIFTY", "daily",
                     pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e6}, index=IDX),
                     "test")
    return s


def nth_session(n):
    """The session n trading days after TODAY (n >= 1)."""
    return IDX[IDX > TODAY][n - 1]


def test_size_gate_rule(tmp_path):
    s = setup(tmp_path)
    events.store_events(s, [
        {"symbol": "FAR", "d": str(nth_session(20).date()), "kind": "results"},
        {"symbol": "MID", "d": str(nth_session(4).date()), "kind": "results"},
        {"symbol": "NEAR", "d": str(nth_session(1).date()), "kind": "results"},
    ])
    assert events.sessions_until(s, "FAR", today=TODAY) == 20
    assert events.sessions_until(s, "MID", today=TODAY) == 4
    assert events.sessions_until(s, "NEAR", today=TODAY) == 1
    assert events.sessions_until(s, "UNKNOWN", today=TODAY) is None

    # the plan rule, exercised through the REAL function: no entry <=2, half <=5
    assert events.size_gate(s, "NEAR", 100, today=TODAY)[0] == 0
    events.store_events(s, [{"symbol": "EDGE", "d": str(nth_session(2).date()), "kind": "results"}])
    assert events.size_gate(s, "EDGE", 100, today=TODAY)[0] == 0  # exactly 2 sessions -> blocked
    q, note = events.size_gate(s, "MID", 101, today=TODAY)
    assert q == 50 and "half size" in note  # odd qty rounds DOWN, never up
    q, note = events.size_gate(s, "FAR", 100, today=TODAY)
    assert q == 100 and "20" in note
    q, note = events.size_gate(s, "UNKNOWN", 100, today=TODAY)
    assert q == 100 and "unknown" in note  # blind spot stated, not hidden
    s.close()


def test_store_events_idempotent_and_next_event(tmp_path):
    s = setup(tmp_path)
    d = str(nth_session(10).date())
    assert events.store_events(s, [{"symbol": "TATA", "d": d, "kind": "results"}]) == 1
    assert events.store_events(s, [{"symbol": "TATA", "d": d, "kind": "results"}]) == 1  # replace, not dup
    assert events.next_event(s, "TATA", today=TODAY) == d
    s.close()


def test_missing_index_calendar_is_unknown(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")  # no NIFTY bars
    events.store_events(s, [{"symbol": "TATA", "d": str(TODAY.date()), "kind": "results"}])
    assert events.sessions_until(s, "TATA", today=TODAY) is None  # unknown calendar -> unknown
    q, note = events.size_gate(s, "TATA", 100, today=TODAY)
    assert q == 100 and "unknown" in note  # guard admits it is off, never pretends safe
    s.close()
