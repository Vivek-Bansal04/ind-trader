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
CREATE TABLE IF NOT EXISTS corporate_actions(
    symbol TEXT, d TEXT, kind TEXT, ratio REAL,
    PRIMARY KEY (symbol, d, kind)
);
CREATE TABLE IF NOT EXISTS split_factors(
    symbol TEXT, d TEXT, ratio REAL,
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
            f = self.combined_factors(symbol)
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

    # --- split factors (from NSE corporate-actions records) ---
    def upsert_split_factors(self, symbol: str, factors: dict) -> int:
        """factors: {date_str: ratio} — ratio applied on that date (0.5 for 1:2 split)."""
        rows = [(symbol.upper(), d, float(v)) for d, v in factors.items()]
        self.con.executemany("INSERT OR REPLACE INTO split_factors VALUES (?,?,?)", rows)
        self.con.commit()
        return len(rows)

    def split_factors(self, symbol: str) -> pd.Series:
        r = self.con.execute(
            "SELECT d, ratio FROM split_factors WHERE symbol=? ORDER BY d", [symbol.upper()]).fetchall()
        if not r:
            return pd.Series(dtype="float64")
        return pd.Series([v for _, v in r], index=pd.to_datetime([d for d, _ in r]), name="ratio")

    def combined_factors(self, symbol: str) -> pd.Series:
        """Merge yfinance back-adjustment factors with recorded split ratios —
        WITHOUT double-counting. yfinance records dividends only *in practice*
        (the round-4 HDFCBANK proof), but 'in practice' is not a guarantee, so
        each recorded split is checked against yf's own factor STEP on the
        ex-date: already priced -> skipped (and said so); missing -> its ratio
        applies to days STRICTLY BEFORE the ex-date (the ex-date's own raw
        price is post-split). f[t] = b[t] * prod{ r_D : D > t }, with b anchored
        to 1.0 on the newest day (yfinance convention) and its own level
        carrying history backward. Round-5 trap kept out: days before the FIRST
        stored yf factor take that OLDEST factor level, never an assumed 1.0 —
        a level of 1.0 means 'no adjustment' and would silently erase history."""
        yf = self.adj_factors(symbol)
        sf = self.split_factors(symbol)
        if yf.empty and sf.empty:
            return pd.Series(dtype="float64")

        # day grid: the symbol's own sessions (the factor series must live where
        # the candles are — split days alone would drop every other day), plus
        # the factor dates themselves; yf.index/sf.index are DatetimeIndex only
        # when non-empty (empty factors have an int RangeIndex — union on that
        # silently degrades to object dtype).
        days = pd.DatetimeIndex(pd.to_datetime([r[0] for r in self.con.execute(
            "SELECT DISTINCT d FROM candles WHERE symbol=? AND tf='daily' ORDER BY d",
            [symbol.upper()]).fetchall()]))
        parts = [p for p in (days, yf.index if not yf.empty else None,
                             sf.index if not sf.empty else None) if p is not None]
        idx = parts[0]
        for p in parts[1:]:
            idx = idx.union(p)
        idx = idx.sort_values()

        if yf.empty:
            b = pd.Series(1.0, index=idx)  # split-only adjustment; dividends stay unknown
        else:
            b = yf.reindex(idx).ffill().bfill()
            anchor = float(b.iloc[-1]) if not b.empty else 1.0
            if anchor:  # newest day = 1.0 (yfinance convention: today is always raw)
                b = b / anchor

        if sf.empty:
            return b.rename("factor")
        # Per recorded split on ex-date D with price multiplier r: yf's factor
        # step ON D (b[D]/b[prev]) says whether yf already priced it. Step ~= r
        # -> yf recorded it -> NO extra weight (this is the no-double-counting
        # guarantee, now actually true). Step ~= 1 -> the HDFCBANK reality ->
        # weight r applies to days strictly BEFORE D.
        s = pd.Series(1.0, index=idx)
        skipped_covered = 0
        for D, r in sf.sort_index().items():
            prev = b[b.index < D]
            step = 1.0
            if D in b.index and len(prev):
                step = float(b.loc[D]) / float(prev.iloc[-1])
            # back-adjusted factors step UP by 1/r on an action day — if yf's
            # step matches the recorded split, yf already priced it.
            if abs(step * r - 1.0) <= 0.02:
                skipped_covered += 1
                continue
            s.loc[D] = float(r)
        if skipped_covered:
            print(f"[combined-factors] {symbol}: {skipped_covered} recorded split(s) already "
                  "priced by yfinance factors — not double-applied")
        # extra split weight: for each day t, product of r over split dates > t
        # (the ex-date itself carries NO extra weight — its raw price is post-split)
        suffix = s[::-1].cumprod()[::-1]
        m = suffix / s
        return (b * m).rename("factor")

    def close(self):
        self.con.close()
