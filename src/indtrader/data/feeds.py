"""NSE evidence feeds beyond candles: delivery % (bhavcopy) and bulk/block deals.

All network access goes through the NseIndiaApi provider object passed in — so every
parse/upsert path here is testable offline with a fake object, and when NSE is
unreachable (office laptop!) callers simply keep last-known data. Stored in the
same sqlite store as candles: `data/bars.sqlite`.

Delivery % is a classic Indian swing-signal: a breakout with LOW delivery is a
weaker breakout (mostly intraday churning), high delivery means real hands changed.
Net bulk buying in the last week is a mild positive tilt, never a buy trigger by
itself — it only boosts the scanner score.
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta

import pandas as pd

from indtrader.data.store import BarStore

FEED_SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery_pct(
    symbol TEXT, d TEXT, deliv_qty REAL, deliv_per REAL,
    PRIMARY KEY (symbol, d)
);
CREATE TABLE IF NOT EXISTS deals(
    d TEXT, symbol TEXT, client TEXT, side TEXT, qty REAL, price REAL,
    kind TEXT,  -- bulk | block
    PRIMARY KEY (d, symbol, client, side, qty, price)
);
"""

# NSE delivery bhavcopy column spellings (they have drifted over the years)
_DELIV_ALIASES = {
    "symbol": ("SYMBOL",),
    "date": ("DATE1", "DATE"),
    "ttl_qty": ("TTL_TRD_QNTY", "TTQTRADED", "TOTAL_TRADED_QTY"),
    "deliv_qty": ("DELIV_QTY", "DELIVERY_QTY"),
    "deliv_per": ("DELIV_PER", "DELIVERY_PERSNT"),
}


def parse_delivery_csv(text: str) -> pd.DataFrame:
    """NSE sec_bhavdata_full CSV text -> DataFrame[symbol, d, deliv_qty, deliv_per].

    Robust to missing DELIV_PER (computes from qty ratio) and EQ-only filtered.
    """
    raw = pd.read_csv(io.StringIO(text), dtype=str)
    cols = {c: c.strip().upper() for c in raw.columns}
    raw = raw.rename(columns=cols)
    pick = {}
    for canon, aliases in _DELIV_ALIASES.items():
        for a in aliases:
            if a in raw.columns:
                pick[canon] = a
                break
    if "symbol" not in pick or "date" not in pick:
        return pd.DataFrame(columns=["symbol", "d", "deliv_qty", "deliv_per"])
    out = pd.DataFrame()
    out["symbol"] = raw[pick["symbol"]].str.strip().str.upper()
    out["d"] = pd.to_datetime(raw[pick["date"]], dayfirst=True, errors="coerce").dt.strftime("%Y-%m-%d")
    qty = pd.to_numeric(raw.get(pick.get("ttl_qty"), pd.Series(index=raw.index)), errors="coerce") if pick.get("ttl_qty") else pd.Series(float("nan"), index=raw.index)
    dqty = pd.to_numeric(raw.get(pick.get("deliv_qty"), pd.Series(index=raw.index)), errors="coerce") if pick.get("deliv_qty") else pd.Series(float("nan"), index=raw.index)
    dper = pd.to_numeric(raw.get(pick.get("deliv_per"), pd.Series(index=raw.index)), errors="coerce") if pick.get("deliv_per") else pd.Series(float("nan"), index=raw.index)
    # compute % when absent; keep given value when present
    computed = (dqty / qty * 100).where(qty > 0)
    out["deliv_per"] = dper.fillna(computed)
    out["deliv_qty"] = dqty
    if "SERIES" in raw.columns:
        out = out[raw["SERIES"].astype(str).str.strip() == "EQ"]
    out = out.dropna(subset=["d", "deliv_per"])
    return out[["symbol", "d", "deliv_qty", "deliv_per"]]


def parse_bulk_deals(rows: list[dict]) -> pd.DataFrame:
    """NseIndiaApi bulk_deals rows -> DataFrame[d, symbol, client, side, qty, price, kind].

    Keys are NSE's BD_* codes; unparseable rows are dropped, never fatal.
    """
    recs = []
    for r in rows or []:
        try:
            d_raw = r.get("BD_DT_DATE") or r.get("BD_DT_TIME")
            d = datetime.strptime(str(d_raw)[:10], "%d-%m-%Y").date().isoformat()
            recs.append(
                {
                    "d": d,
                    "symbol": str(r["BD_SYMBOL"]).strip().upper(),
                    "client": str(r.get("BD_CLIENT_NAME", ""))[:120],
                    "side": str(r.get("BD_BUY_SELL", "")).strip().upper()[:1],
                    "qty": float(r.get("BD_QTY_TRD") or 0),
                    "price": float(r.get("BD_TP_WATP") or 0),
                    "kind": "bulk",
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return pd.DataFrame(recs, columns=["d", "symbol", "client", "side", "qty", "price", "kind"])


def ensure_feed_tables(store: BarStore) -> None:
    store.con.executescript(FEED_SCHEMA)


def upsert_delivery(store: BarStore, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    ensure_feed_tables(store)
    rows = [(r.symbol, r.d, None if pd.isna(r.deliv_qty) else float(r.deliv_qty), float(r.deliv_per)) for r in df.itertuples()]
    store.con.executemany("INSERT OR REPLACE INTO delivery_pct VALUES (?,?,?,?)", rows)
    store.con.commit()
    return len(rows)


def upsert_deals(store: BarStore, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    ensure_feed_tables(store)
    rows = [(r.d, r.symbol, r.client, r.side, float(r.qty), float(r.price), r.kind) for r in df.itertuples()]
    store.con.executemany("INSERT OR REPLACE INTO deals VALUES (?,?,?,?,?,?,?)", rows)
    store.con.commit()
    return len(rows)


def fetch_delivery_day(nse_obj, store: BarStore, on: date) -> int:
    """nse_obj = live NseIndiaApi NSE instance (context-managed by caller)."""
    path = nse_obj.delivery_bhavcopy(date=on)
    text = open(path, encoding="utf-8", errors="replace").read()
    return upsert_delivery(store, parse_delivery_csv(text))


def fetch_bulk_deals(nse_obj, store: BarStore, days: int = 7) -> int:
    to_d = date.today()
    from_d = to_d - timedelta(days=days)
    rows = nse_obj.bulk_deals("bulk_deals", from_date=from_d, to_date=to_d)
    return upsert_deals(store, parse_bulk_deals(rows))


def index_universe(nse_obj, index: str = "nifty 50") -> list[str]:
    """Symbols of index constituents. First entry of NSE's payload is the index
    itself — skipped. Symbols returned un-suffixed (NSE-native)."""
    payload = nse_obj.list_equity_stocks_by_index(index=index)
    entries = payload.get("data", []) if isinstance(payload, dict) else payload
    out, seen = [], set()
    for e in entries or []:
        sym = str(e.get("symbol", "")).strip().upper()
        ident = str(e.get("identifier", "")).strip().upper()
        if not sym or sym == ident or sym == index.upper() or " " in sym:
            continue  # skip the index row and weird entries
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


# ---------------------------------------------------------------- read side

def latest_delivery_per(store: BarStore, symbol: str) -> float | None:
    ensure_feed_tables(store)
    r = store.con.execute(
        "SELECT deliv_per FROM delivery_pct WHERE symbol=? ORDER BY d DESC LIMIT 1", [symbol.upper()]
    ).fetchone()
    return round(float(r[0]), 1) if r else None


def avg_delivery_per(store: BarStore, symbol: str, days: int = 20) -> float | None:
    ensure_feed_tables(store)
    r = store.con.execute(
        "SELECT AVG(deliv_per) FROM delivery_pct WHERE symbol=? AND d >= "
        "DATE((SELECT MAX(d) FROM delivery_pct WHERE symbol=?), ?)",
        [symbol.upper(), symbol.upper(), f"-{days} days"],
    ).fetchone()
    return round(float(r[0]), 1) if r and r[0] is not None else None


def net_bulk_value(store: BarStore, symbol: str, days: int = 7) -> float | None:
    """(buy - sell) rupee value of bulk deals in the window; None if never ingested
    (unknown is different from zero!)."""
    ensure_feed_tables(store)
    if not store.con.execute("SELECT COUNT(*) FROM deals").fetchone()[0]:
        return None
    r = store.con.execute(
        "SELECT SUM(CASE WHEN side='B' THEN qty*price ELSE -qty*price END) FROM deals "
        "WHERE symbol=? AND d >= DATE('now', ?)",
        [symbol.upper(), f"-{days} days"],
    ).fetchone()
    return round(float(r[0] or 0.0), 0)


# ---------------------------------------------------------------- nightly fetch

def fetch_feeds(store: BarStore | None = None, lookback_days: int = 7) -> dict:
    """One evening's feed refresh via NseIndiaApi. Safe to call anywhere: when the
    package isn't installed or NSE is unreachable (office laptop), it reports that
    and keeps the last-known data — feeds are best-effort evidence, never critical."""
    from indtrader.data.store import BarStore as _BS

    store = store or _BS()
    result = {"delivery_rows": 0, "deal_rows": 0, "status": "skipped:no-nseapi"}
    try:
        from nse import NSE  # noqa: F401 — availability probe only
    except ImportError:
        print("[feeds] NseIndiaApi not installed — skipping feeds (pip install NseIndiaApi)")
        return result
    from nse import NSE
    import tempfile
    from pathlib import Path

    folder = Path(tempfile.mkdtemp(prefix="nsefeeds_"))
    try:
        with NSE(download_folder=str(folder)) as nse:
            # delivery bhavcopy: today, walking back a few days over weekends/holidays
            for back in range(0, 5):
                d = date.today() - timedelta(days=back)
                try:
                    result["delivery_rows"] = fetch_delivery_day(nse, store, d)
                    result["delivery_date"] = d.isoformat()
                    break
                except Exception:
                    continue  # not published yet / holiday — try previous day
            try:
                result["deal_rows"] = fetch_bulk_deals(nse, store, days=lookback_days)
            except Exception as e:
                print(f"[feeds] bulk deals unavailable: {e}")
        result["status"] = "ok"
    except Exception as e:
        result["status"] = f"unreachable:{type(e).__name__}"
        print(f"[feeds] NSE unreachable (fine on the office laptop): {e}")
    return result
