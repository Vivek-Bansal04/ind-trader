"""Data-source contract. Everything outside this file knows NOTHING about NSE/Yahoo/brokers.

Providers: yfinance (works today, .NS suffix), broker adapters land in Phase 1 as
separate classes (NseIndiaApi is the planned primary; see build-plan.md).
"""
from __future__ import annotations

from typing import Protocol

import pandas as pd

from indtrader.models import CANDLE_COLUMNS, is_valid_candle


class DataSource(Protocol):
    name: str

    def daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """OHLCV frame, DatetimeIndex ascending, columns CANDLE_COLUMNS. [] or raise."""
        ...


def clean_daily(df: pd.DataFrame, symbol: str, max_gap_warn: int = 3) -> pd.DataFrame:
    """Validate OHLCV rows, drop dupes/non-positives, flag suspicious jumps loudly."""
    if df is None or df.empty:
        return pd.DataFrame(columns=CANDLE_COLUMNS)
    out = df.loc[:, [c for c in CANDLE_COLUMNS if c in df.columns]].copy()
    out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    has_vol = "volume" in out.columns
    mask = [
        is_valid_candle(r.open, r.high, r.low, r.close, r.volume if has_vol else 0.0)
        for r in out.itertuples()
    ]
    out = out[mask]
    out = out[(out["close"] > 0) & (out["volume"] >= 0)]
    jumps = out["close"].pct_change().abs()
    bad = out.index[jumps > 0.35]
    for ts in bad:
        print(f"[data-quality] {symbol}: >35% jump at {ts.date()} — verify corporate action manually")
    d = out.index.to_series().diff().dt.days.dropna()
    if not d.empty and d.max() > max_gap_warn * 4:  # >~ a week+ of calendar gap
        print(f"[data-quality] {symbol}: max gap {int(d.max())} days — possible missing sessions")
    return out


_INDEX_MAP = {"NIFTY": "^NSEI"}


def suffix_nse(symbol: str) -> str:
    s = symbol.upper().strip()
    if s in _INDEX_MAP:
        return _INDEX_MAP[s]
    return s if "." in s else f"{s}.NS"
