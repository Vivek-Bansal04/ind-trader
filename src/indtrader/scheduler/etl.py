"""Nightly ETL: provider -> validation -> store. Cron 16:00 IST Sun-Thu.
No scheduler library yet (APScheduler joins Phase 1 with broker APIs)."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from threading import Lock

IST = timezone(timedelta(hours=5, minutes=30))

import pandas as pd

from indtrader.config import Settings, get_settings
from indtrader.data.base import DataSource
from indtrader.data.store import BarStore
from indtrader.data.universe import load_universe
from indtrader.data.yfinance_provider import YFinanceProvider


_store_lock = Lock()


def _fetch_one(sym: str, providers: list[DataSource], start: str, end: str,
               pause_s: float) -> tuple[str, object, str | None]:
    """Fetch daily candles for one symbol from the first provider that yields data."""
    for prov in providers:
        try:
            df = prov.daily(sym, start, end)
        except Exception:
            continue
        if df is not None and not df.empty:
            return sym, df, prov.name
        time.sleep(pause_s)
    return sym, None, None


def build_providers(settings: Settings) -> list[DataSource]:
    """Provider chain by name; unavailable providers are skipped quietly but logged.
    nseapi = NseIndiaApi (primary on a normal network), yfinance = fallback."""
    reg: dict[str, DataSource] = {}
    from indtrader.data.nseapi_provider import NseIndiaApiProvider

    if NseIndiaApiProvider.available():
        reg["nseapi"] = NseIndiaApiProvider()
    else:
        print("[etl] NseIndiaApi not installed — skipping 'nseapi' (pip install NseIndiaApi)")
    from indtrader.data.yfinance_provider import YFinanceProvider

    reg["yfinance"] = YFinanceProvider()

    out = []
    for name in [p.strip() for p in settings.data_provider_order.split(",") if p.strip()]:
        if name in reg:
            out.append(reg[name])
        elif name != "nseapi":  # nseapi absence already announced
            print(f"[etl] unknown provider '{name}' in DATA_PROVIDER_ORDER — skipped")
    if not out:
        out = [YFinanceProvider()]
    return out


def _history_shape(store: BarStore, symbol: str) -> tuple[str | None, str | None, int]:
    """First/last session dates and row count straight from sqlite — cheap enough
    to run per symbol without loading candles."""
    row = store.con.execute(
        "SELECT min(d), max(d), count(*) FROM candles WHERE symbol=? AND tf='daily'",
        [symbol.upper()]).fetchone()
    return (row[0], row[1], int(row[2] or 0)) if row else (None, None, 0)


def _is_complete_and_fresh(store: BarStore, symbol: str, start: str,
                           fresh_bdays: int, today: date) -> bool:
    """Skip candidate: tail sits ON the latest SETTLED session the NIFTY calendar
    confirms AND (history reaching back to ~start OR a genuine new listing: first
    bar after start with a real current tail).

    Round-8 correction (the user's "missed two evenings" question): the old rule
    skipped any tail within `fresh_bdays` business days — a 1-2 day hole was
    'fresh', so missed evenings self-healed only once the gap grew past the
    tolerance constant. The correct anchor is the exchange's own calendar: a
    tail is fresh iff it holds the latest settled session; anything older is a
    hole and refetches (the provider returns full history, so the refill costs
    one call). Weekends/holidays need no special-casing — NIFTY has no bar for
    them either, so Friday's tail is fresh on Saturday morning and stale by
    Monday evening, exactly like it should be. Settled = not today while the
    clock is before 16:15 IST: today-stamped bars are intraday snapshots and
    always refetch (round-6 live: a 14:14 dry run froze mid-session closes)."""
    first, last, n = _history_shape(store, symbol)
    if n == 0 or not last:
        return False
    now = datetime.now(IST)
    expected = _expected_session(store, now)
    if expected is None:
        # No NIFTY calendar stored (bootstrap run): strictness requires proof, so
        # the LEGACY tolerance governs here — busday gap within fresh_bdays and
        # no unsettled today-stamp. The calendar rule takes over the moment the
        # index has bars; until then we are as lenient as the old code and say
        # nothing louder than "bootstrap".
        import numpy as np

        if int(np.busday_count(pd.Timestamp(last).date(), today)) > fresh_bdays:
            return False
        settled_cutoff = (now.date() if (now.hour, now.minute) < (16, 15)
                          else now.date() - timedelta(days=1))
        if pd.Timestamp(last).date() > settled_cutoff:
            return False
    elif pd.Timestamp(last).date() < expected:
        return False  # calendar-confirmed hole of ANY size -> refetch and heal
    if first is None:
        return False
    if (pd.Timestamp(first).date() - pd.Timestamp(start).date()).days <= 45:
        return True
    return n >= NEW_LISTING_MIN_ROWS


NEW_LISTING_MIN_ROWS = 60  # ~3 months of dailies: shallow-but-current is suspect


def _expected_session(store: BarStore, now: datetime) -> date | None:
    """The session date a complete tail should carry: the latest SETTLED session
    NIFTY's own stored bars confirm. None with no stored NIFTY bars — the caller
    must then fall back, never assume."""
    row = store.con.execute(
        "SELECT max(d) FROM candles WHERE symbol='NIFTY' AND tf='daily'").fetchone()
    if not row or not row[0]:
        return None
    cutoff = now.date() - timedelta(days=1) if (now.hour, now.minute) < (16, 15) else now.date()
    return min(pd.Timestamp(row[0]).date(), cutoff)


def fetch_universe(symbols: list[str], store: BarStore | None = None, providers: list[DataSource] | None = None,
                   start: str = "2015-01-01", pause_s: float = 0.3,
                   skip_fresh: bool = False, fresh_bdays: int = 3,
                   max_workers: int = 5) -> dict[str, int | None]:
    """Fetch (or top up) each symbol -> {sym: rows_stored, or None when skipped
    as fresh}. skip_fresh=True makes the nightly loop and a
    resumed backfill cheap: a symbol is skipped when its tail is current AND it
    either reaches ~start or is a genuine new listing (first bar after start with
    >= 60 bars — re-fetching cannot fill pre-listing history). Both conditions
    required for the old-world case — 'fresh tail' alone would declare a shallow
    series done, and that's the kind of quiet shortening that fakes a backtest.
    Default off: an explicit `fetch` always fetches."""
    providers = providers or build_providers(get_settings())
    store = store or BarStore()
    results: dict[str, int | None] = {}
    end = (date.today() + timedelta(days=1)).isoformat()
    today = date.today()

    # freshness pre-filter (skip_fresh path)
    todo: list[str] = []
    for sym in symbols:
        if skip_fresh and _is_complete_and_fresh(store, sym, start, fresh_bdays, today):
            _, last, n = _history_shape(store, sym)
            print(f"[etl] {sym}: fresh ({n} rows, last bar {last}) — skipped")
            results[sym] = None  # NOT fetched — distinct from 0 ("fetched, no data")
            continue
        todo.append(sym)

    # parallel fetch for the rest
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, sym, providers, start, end, pause_s): sym
                   for sym in todo}
        for future in as_completed(futures):
            sym, df, prov_name = future.result()
            if df is not None and not df.empty:
                with _store_lock:
                    rows = store.upsert_candles(sym, "daily", df, prov_name)
                results[sym] = rows
                print(f"[etl] {sym}: {rows} rows stored")
            else:
                results[sym] = 0
                print(f"[etl] {sym}: 0 rows stored (no data)")
    return results


def run_etl(universe_file=None, store: BarStore | None = None) -> dict[str, int | None]:
    symbols = load_universe(universe_file)
    return fetch_universe(symbols, store=store, skip_fresh=True)


def fetch_adj_factors(symbols: list[str], store: BarStore | None = None,
                      start: str = "2015-01-01", pause_s: float = 0.3) -> dict[str, int]:
    """Corporate-action factors (Adj Close/Close) from yfinance, stored next to raw
    candles — raw stays the source of truth; factors are the derived layer. Symbols
    whose factor feed is empty report 0, loudly — never a silent factor of 1.0."""
    store = store or BarStore()
    prov = YFinanceProvider()
    end = (date.today() + timedelta(days=1)).isoformat()
    out: dict[str, int] = {}
    for sym in symbols:
        try:
            f = prov.daily_adj_factors(sym, start, end)
        except Exception as e:
            print(f"[etl-adj] {sym}: factor fetch failed: {e}")
            out[sym] = 0
            continue
        if f.empty:
            print(f"[etl-adj] {sym}: no factor data from yfinance — adjusted reads stay RAW for it")
        else:
            store.upsert_adj_factors(sym, f, prov.name)
        out[sym] = int(f.size)
        time.sleep(pause_s)
    return out
