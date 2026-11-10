"""Trading calendar — derived, never hardcoded.

Hardcoding an exchange holiday list is a lie waiting to expire (and a fail-loud
violation: we'd be guessing dates we can't verify offline). The index's own bar
sequence IS the authoritative calendar of sessions that actually happened, so the
store's NIFTY dates are the calendar; this module only answers questions about it.
"""
from __future__ import annotations

import pandas as pd

DATE_KEYS = ("d", "date", "Date")


def _to_dates(s: pd.Series | pd.DatetimeIndex) -> pd.DatetimeIndex:
    if isinstance(s, pd.DatetimeIndex):
        return s.normalize().unique().sort_values()
    d = pd.to_datetime(pd.Series(s).dropna()).dt.normalize()
    return pd.DatetimeIndex(sorted(d.unique()))


def index_sessions(df_or_dates: pd.DataFrame | pd.Series | pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Sessions from an index frame (columns d/date/Date or a DatetimeIndex), or a dates series."""
    if isinstance(df_or_dates, pd.DataFrame):
        for k in DATE_KEYS:
            if k in df_or_dates.columns:
                return _to_dates(df_or_dates[k])
        return _to_dates(df_or_dates.index)
    return _to_dates(pd.Series(df_or_dates) if not isinstance(df_or_dates, pd.DatetimeIndex)
                     else df_or_dates)


def missing_sessions(stock: pd.DataFrame | pd.Series | pd.DatetimeIndex,
                     sessions: pd.DatetimeIndex) -> pd.DatetimelikeArray:
    """Index sessions absent from the stock WITHIN the stock's own first..last span.
    Spans outside are not 'missing' (not listed yet / delisted) — unknown, not zero."""
    d = index_sessions(stock)
    if len(d) == 0 or len(sessions) == 0:
        return pd.DatetimeIndex([])
    span = sessions[(sessions >= d.min()) & (sessions <= d.max())]
    return span.difference(d)


def staleness_days(stock: pd.DataFrame | pd.Series | pd.DatetimeIndex,
                   sessions: pd.DatetimeIndex) -> int | None:
    """Sessions between the stock's last bar and the market's last session (None if
    either side is unknown)."""
    d = index_sessions(stock)
    if len(d) == 0 or len(sessions) == 0:
        return None
    return int(((sessions > d.max()) & (sessions <= sessions.max())).sum())
