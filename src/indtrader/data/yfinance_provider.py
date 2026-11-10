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

    def daily_adj_factors(self, symbol: str, start: str, end: str) -> pd.Series:
        """Adj Close / Close ratio per session = the corporate-action factor yfinance
        already computes for us (HOME-VALIDATION §4 checks its shape on real splits)."""
        import yfinance as yf

        t = yf.Ticker(suffix_nse(symbol))
        df = t.history(start=start, end=end, auto_adjust=False, interval="1d")
        if df is None or df.empty or "Adj Close" not in df or "Close" not in df:
            return pd.Series(dtype="float64")  # unknown, not zero — caller stores nothing
        close = pd.to_numeric(df["Close"], errors="coerce")
        adj = pd.to_numeric(df["Adj Close"], errors="coerce")
        out = (adj / close).dropna()
        if getattr(out.index, "tz", None) is not None:
            out.index = out.index.tz_localize(None)
        return out
