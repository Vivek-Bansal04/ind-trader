"""Core data types. Everything that moves between modules is validated here once."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

CANDLE_COLUMNS = ["open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class Candle:
    dt: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class OrderPlan:
    """A written plan for one position. Executions that deviate from a plan are bugs.

    CNC/delivery only — there is deliberately no product/timeframe field to flip
    to intraday or margin.
    """

    symbol: str
    action: str  # BUY | SELL (delivery)
    qty: int
    entry: float  # planned entry price (limit)
    stop: float  # plan stop (GTT-style mental stop; enforced by review, not by bot)
    target: float
    reason: str  # mandatory written rationale — surfaced in every report
    setup: str = ""  # breakout | pullback | rotation-entry | manual
    created_on: date | None = None
    ttl_days: int = 2  # a stale signal is a cancelled signal


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str  # BUY | SELL
    qty: int
    price: float
    costs: float  # total all-in Indian costs for this fill
    when: datetime


def candles_frame(rows: list[Candle]) -> pd.DataFrame:
    """Candles -> canonical OHLCV frame: date index, ascending, numeric columns."""
    if not rows:
        return pd.DataFrame(columns=CANDLE_COLUMNS)
    df = pd.DataFrame([r.__dict__ for r in rows]).rename(columns={"dt": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()[CANDLE_COLUMNS].astype("float64")
    return df


def is_valid_candle(o: float, h: float, l: float, c: float, v: float) -> bool:
    """Exchange-plausible OHLCV. Used both when ingesting and when reading back."""
    vals = (o, h, l, c, v)
    if any(pd.isna(x) for x in vals):
        return False
    if o <= 0 or h <= 0 or l <= 0 or c <= 0 or v < 0:
        return False
    return h >= max(o, c) and l <= min(o, c)
