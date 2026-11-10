"""yfinance provider — daily/weekly history for NSE (.NS) symbols. Free tier, adequate
for research/backfill until the NseIndiaApi adapter becomes primary (Phase 1)."""
from __future__ import annotations

import pandas as pd

from indtrader.data.base import clean_daily, suffix_nse


class YFinanceProvider:
    name = "yfinance"

    def daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        import yfinance as yf

        t = yf.Ticker(suffix_nse(symbol))
        df = t.history(start=start, end=end, auto_adjust=False, interval="1d")
        if df is None or df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = df.rename(
            columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
        )
        df = df[["open", "high", "low", "close", "volume"]]
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)
        return clean_daily(df, symbol)
