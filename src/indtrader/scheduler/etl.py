"""Nightly ETL: provider -> validation -> store. Cron 16:00 IST Sun-Thu.
No scheduler library yet (APScheduler joins Phase 1 with broker APIs)."""
from __future__ import annotations

import time
from datetime import date, timedelta

from indtrader.config import Settings, get_settings
from indtrader.data.base import DataSource
from indtrader.data.store import BarStore
from indtrader.data.universe import load_universe
from indtrader.data.yfinance_provider import YFinanceProvider


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


def fetch_universe(symbols: list[str], store: BarStore | None = None, providers: list[DataSource] | None = None,
                   start: str = "2015-01-01", pause_s: float = 0.3) -> dict[str, int]:
    providers = providers or build_providers(get_settings())
    store = store or BarStore()
    results: dict[str, int] = {}
    end = (date.today() + timedelta(days=1)).isoformat()
    for sym in symbols:
        rows = 0
        for prov in providers:  # first provider that yields data wins
            try:
                df = prov.daily(sym, start, end)
            except Exception as e:
                print(f"[etl] {sym}: provider {prov.name} failed: {e}")
                continue
            if df is not None and not df.empty:
                rows = store.upsert_candles(sym, "daily", df, prov.name)
                break
            time.sleep(pause_s)
        results[sym] = rows
        print(f"[etl] {sym}: {rows} rows stored")
        time.sleep(pause_s)
    return results


def run_etl(universe_file=None, store: BarStore | None = None) -> dict[str, int]:
    symbols = load_universe(universe_file)
    return fetch_universe(symbols, store=store)
