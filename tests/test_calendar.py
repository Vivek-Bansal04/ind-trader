import numpy as np
import pandas as pd

from indtrader.research.calendar import index_sessions, missing_sessions, staleness_days

SESSIONS = pd.bdate_range("2024-01-01", periods=100).drop(
    pd.to_datetime(["2024-01-26", "2024-03-25", "2024-05-01"]))  # republic/day after/monday holidays


def stock(dates):
    return pd.DataFrame({"close": np.linspace(100, 120, len(dates))}, index=pd.DatetimeIndex(dates))


def test_index_sessions_from_frame_index_and_date_column():
    s1 = index_sessions(stock(SESSIONS))
    df_col = pd.DataFrame({"d": SESSIONS, "close": 1.0})
    assert index_sessions(df_col).equals(s1)
    assert len(s1) == len(SESSIONS)


def test_missing_sessions_only_within_own_span():
    full = stock(SESSIONS)
    assert len(missing_sessions(full, SESSIONS)) == 0
    # dropped 3 interior sessions -> exactly those 3
    hole = SESSIONS.drop(pd.to_datetime(["2024-02-14", "2024-02-15", "2024-04-17"]))
    assert set(missing_sessions(stock(hole), SESSIONS)) == {
        pd.Timestamp("2024-02-14"), pd.Timestamp("2024-02-15"), pd.Timestamp("2024-04-17")}
    # listed late: sessions BEFORE first bar are not "missing"
    late = SESSIONS[SESSIONS >= SESSIONS[-30]]
    assert len(missing_sessions(stock(late), SESSIONS)) == 0


def test_staleness_counts_sessions_not_calendar_days():
    lag = SESSIONS[:-5]
    assert staleness_days(stock(lag), SESSIONS) == 5
    assert staleness_days(stock(SESSIONS), SESSIONS) == 0
    assert staleness_days(stock([]), SESSIONS) is None
