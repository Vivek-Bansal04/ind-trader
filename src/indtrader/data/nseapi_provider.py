"""NseIndiaApi provider (primary on a normal network) — Phase 1 of build-plan.

Design note: we do NOT vendor BennyThadikaran/NseIndiaApi (GPL-3.0) into this repo.
We import it as an optional dependency instead, so the two licenses stay at arm's
length. If the package isn't installed, the ETL falls through to yfinance — nothing
breaks, which is exactly how provider chains should behave.

Install on your personal laptop:
    pip install NseIndiaApi        # or: pip install "indtrader[nse]"
Note: NSE blocks datacenter/corporate-proxy egress; expect this provider to work
from home networks, not the office laptop.
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from indtrader.data.base import clean_daily


class NseIndiaApiProvider:
    name = "nseapi"

    def __init__(self, download_folder: Path | None = None):
        self.download_folder = str(download_folder or Path(tempfile.mkdtemp(prefix="nseapi_")))

    @staticmethod
    def available() -> bool:
        import importlib.util

        return importlib.util.find_spec("nse") is not None

    def client(self):
        """Open the underlying NSE client (new-generation library) with the
        download_folder ritual handled — the doc probes and CLI paths use this
        instead of repeating the constructor call (live crashes came from that)."""
        from nse import NSE

        return NSE(download_folder=self.download_folder)

    def daily(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        from nse import NSE  # lazy: keeps import optional

        from_d = self._as_date(start)
        to_d = self._as_date(end)
        with NSE(download_folder=self.download_folder) as nse:
            rows = nse.fetch_equity_historical_data(symbol.upper(), from_date=from_d, to_date=to_d)
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        records = []
        for r in rows:
            try:
                records.append(
                    {
                        "date": pd.Timestamp(r["CH_TIMESTAMP"]),
                        "open": float(r["CH_OPENING_PRICE"]),
                        "high": float(r["CH_TRADE_HIGH_PRICE"]),
                        "low": float(r["CH_TRADE_LOW_PRICE"]),
                        "close": float(r["CH_CLOSING_PRICE"]),
                        "volume": float(r["CH_TOT_TRADED_QTY"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue  # partial rows from NSE are dropped, loud enough via row-count check
        if not records:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(records).set_index("date").sort_index()
        return clean_daily(df, symbol)

    @staticmethod
    def _as_date(value: str) -> date:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
