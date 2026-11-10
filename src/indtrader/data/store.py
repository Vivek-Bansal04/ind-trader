"""SQLite bar store (stdlib — deliberately no duckdb/pyarrow: unsigned binaries are
blocked by Smart App Control on this machine, and stdlib sqlite is plenty at this scale).

The store is the single source of truth. Writes are idempotent upserts and drop rows
failing OHLC validation; reads re-validate — reading our own data is not automatically safe.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from indtrader.config import get_settings
from indtrader.models import is_valid_candle

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles(
    symbol TEXT, tf TEXT, d TEXT,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    source TEXT,
    PRIMARY KEY (symbol, tf, d)
);
CREATE TABLE IF NOT EXISTS ingest_runs(
    symbol TEXT, tf TEXT, run_at TEXT, rows INT, source TEXT
);
CREATE TABLE IF NOT EXISTS adj_factors(
    symbol TEXT, d TEXT, factor REAL, source TEXT,
    PRIMARY KEY (symbol, d)
);
"""


class BarStore:
    def __init__(self, path: Path | None = None):
        settings = get_settings()
        self.path = Path(path) if path else settings.data_dir / "bars.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path))
        self.con.executescript(SCHEMA)

    def upsert_candles(self, symbol: str, tf: str, df: pd.DataFrame, source: str) -> int:
        if df is None or df.empty:
            return 0
        rows = []
        for ts, r in df.iterrows():
            if not is_valid_candle(r["open"], r["high"], r["low"], r["close"], r["volume"]):
                continue
            rows.append(
                (symbol.upper(), tf, pd.Timestamp(ts).date().isoformat(),
                 float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"]), source)
            )
        if not rows:
            return 0
        self.con.executemany(
            "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?)", rows
        )
        self.con.execute(
            "INSERT INTO ingest_runs VALUES (?, ?, datetime('now'), ?, ?)",
            [symbol.upper(), tf, len(rows), source],
        )
        self.con.commit()
        return len(rows)

    def load(self, symbol: str, tf: str = "daily", start: str | None = None, end: str | None = None,
             validate: bool = True, adjusted: bool = False) -> pd.DataFrame:
        q = "SELECT d, open, high, low, close, volume FROM candles WHERE symbol=? AND tf=?"
        args: list = [symbol.upper(), tf]
        if start:
            q += " AND d >= ?"
            args.append(str(pd.Timestamp(start).date()))
        if end:
            q += " AND d <= ?"
            args.append(str(pd.Timestamp(end).date()))
        q += " ORDER BY d"
        fetched = self.con.execute(q, args).fetchall()
        if not fetched:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(fetched, columns=["d", "open", "high", "low", "close", "volume"])
        df["d"] = pd.to_datetime(df["d"])
        df = df.set_index("d")
        if validate:
            mask = [is_valid_candle(*row) for row in df[["open", "high", "low", "close", "volume"]].to_numpy()]
            bad = int((~pd.Series(mask, index=df.index)).sum())
            if bad:
                print(f"[data-quality] {symbol}: dropped {bad} invalid rows on read")
                df = df[mask]
        if adjusted:
            f = self.adj_factors(symbol)
            if f.empty:
                print(f"[data-quality] {symbol}: no adjustment factors stored — returning RAW prices "
                      "instead of pretending the series is adjusted")
            else:
                # normalised so the most recent price stays the actual traded price
                scale = (f.reindex(df.index, method="ffill").ffill() / f.iloc[-1]).to_numpy()
                for col in ("open", "high", "low", "close"):
                    df[col] = df[col].to_numpy() * scale
        return df

    def missing_days(self, symbol: str, tf: str = "daily", start: str = "2015-01-01") -> int:
        """Rough gap detector: Mon-Fri calendar days implied vs stored rows.
        Holidays make this a warning signal, not an error — negative means 'more rows than
        weekdays', impossible unless dates are wrong."""
        df = self.load(symbol, tf, start=start, validate=False)
        if df.empty:
            return -1
        expected = pd.bdate_range(df.index[0], df.index[-1])
        return len(expected) - len(df)

    def last_date(self, symbol: str, tf: str = "daily"):
        r = self.con.execute("SELECT max(d) FROM candles WHERE symbol=? AND tf=?", [symbol.upper(), tf]).fetchone()
        return pd.Timestamp(r[0]).date() if r and r[0] else None

    # --- corporate-action adjustment factors (raw prices stay untouched) ---
    def upsert_adj_factors(self, symbol: str, factors: pd.Series, source: str) -> int:
        """factors indexed by date: the ratio split/dividend-adjusted close / raw close."""
        rows = [(symbol.upper(), pd.Timestamp(ts).date().isoformat(), float(v), source)
                for ts, v in factors.dropna().items()]
        self.con.executemany("INSERT OR REPLACE INTO adj_factors VALUES (?,?,?,?)", rows)
        self.con.commit()
        return len(rows)

    def adj_factors(self, symbol: str) -> pd.Series:
        r = self.con.execute(
            "SELECT d, factor FROM adj_factors WHERE symbol=? ORDER BY d", [symbol.upper()]).fetchall()
        if not r:
            return pd.Series(dtype="float64")
        return pd.Series([v for _, v in r], index=pd.to_datetime([d for d, _ in r]), name="factor")

    def close(self):
        self.con.close()
