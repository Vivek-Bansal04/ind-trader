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
        from_d = self._as_date(start)
        to_d = self._as_date(end)
        with self.client() as nse:
            rows = nse.fetch_equity_historical_data(symbol.upper(), from_date=from_d, to_date=to_d)
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        records, dropped = [], 0
        # 2026-11-11 live probe (round 3): the archive endpoint's keys moved from
        # CH_* to camelCase (chClosingPrice, mtimestamp, ...). Accept BOTH spellings
        # — a library or exchange rollback must not turn into a silent 0-row feed.
        KEY_CANDS = {
            "date": ("mtimestamp", "CH_TIMESTAMP"),
            "open": ("chOpeningPrice", "CH_OPENING_PRICE"),
            "high": ("chTradeHighPrice", "CH_TRADE_HIGH_PRICE"),
            "low": ("chTradeLowPrice", "CH_TRADE_LOW_PRICE"),
            "close": ("chClosingPrice", "CH_CLOSING_PRICE"),
            "volume": ("chTotTradedQty", "CH_TOT_TRADED_QTY"),
        }

        def pick(row: dict, field: str):
            for k in KEY_CANDS[field]:
                if k in row and row[k] is not None:
                    return row[k]
            raise KeyError(f"{field} (tried {KEY_CANDS[field]})")

        for r in rows:
            try:
                records.append(
                    {
                        "date": pd.Timestamp(pick(r, "date")),
                        "open": float(pick(r, "open")),
                        "high": float(pick(r, "high")),
                        "low": float(pick(r, "low")),
                        "close": float(pick(r, "close")),
                        "volume": float(pick(r, "volume")),
                    }
                )
            except (KeyError, TypeError, ValueError):
                dropped += 1
        if records and dropped > len(records):
            # partial key drift: report but keep what parsed
            print(f"[nseapi] {symbol}: dropped {dropped}/{dropped + len(records)} rows on key parse")
        if not records:
            # rows arrived but NOTHING parsed — the response-key contract changed.
            # Returning empty here would silently fake "no such data" (the round-1
            # "0 rows" mystery was exactly this). Name the keys instead.
            sample = rows[0] if isinstance(rows[0], dict) else type(rows[0]).__name__
            raise RuntimeError(
                f"nseapi {symbol}: {len(rows)} rows arrived but 0 parsed — response keys "
                f"changed; first row keys: {sorted(sample) if isinstance(sample, dict) else sample}")
        df = pd.DataFrame(records).set_index("date").sort_index()
        return clean_daily(df, symbol)

    @staticmethod
    def _as_date(value: str) -> date:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
