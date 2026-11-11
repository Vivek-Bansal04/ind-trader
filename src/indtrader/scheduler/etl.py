"""Nightly ETL: provider -> validation -> store. Cron 16:00 IST Sun-Thu.
No scheduler library yet (APScheduler joins Phase 1 with broker APIs)."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from threading import Lock

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
    """Skip candidate: tail within fresh_bdays business days of today AND history
    reaching back to ~start (so a fresh-tailed but shallow series is refetched,
    not declared done). Missing on either side -> False -> fetch."""
    import numpy as np

    first, last, n = _history_shape(store, symbol)
    if n == 0 or not last:
        return False
    if int(np.busday_count(pd.Timestamp(last).date(), today)) > fresh_bdays:
        return False
    return first is not None and (pd.Timestamp(first).date()
                                  - pd.Timestamp(start).date()).days <= 45


def fetch_universe(symbols: list[str], store: BarStore | None = None, providers: list[DataSource] | None = None,
                   start: str = "2015-01-01", pause_s: float = 0.3,
                   skip_fresh: bool = False, fresh_bdays: int = 3,
                   max_workers: int = 5) -> dict[str, int]:
    """Fetch (or top up) each symbol. skip_fresh=True makes the nightly loop and a
    resumed backfill cheap: a symbol whose tail is current AND whose history
    reaches ~start is skipped outright. Both conditions required — 'fresh tail'
    alone would declare a shallow series done, and that's the kind of quiet
    shortening that fakes a backtest. Default off: an explicit `fetch` always fetches."""
    providers = providers or build_providers(get_settings())
    store = store or BarStore()
    results: dict[str, int] = {}
    end = (date.today() + timedelta(days=1)).isoformat()
    today = date.today()

    # freshness pre-filter (skip_fresh path)
    todo: list[str] = []
    for sym in symbols:
        if skip_fresh and _is_complete_and_fresh(store, sym, start, fresh_bdays, today):
            _, last, n = _history_shape(store, sym)
            print(f"[etl] {sym}: fresh ({n} rows, last bar {last}) — skipped")
            results[sym] = 0
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


def run_etl(universe_file=None, store: BarStore | None = None) -> dict[str, int]:
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
