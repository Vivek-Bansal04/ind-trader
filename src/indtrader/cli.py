"""Command entry points (stdlib argparse; typer is a Phase-1 upgrade).

  indtrader fetch RELIANCE | fetch-universe
  indtrader backtest RELIANCE --strategy donchian --costs
  indtrader rrg RELIANCE TCS INFY --bench NIFTY
  indtrader regime
  indtrader ai RELIANCE
"""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from indtrader.config import get_settings
from indtrader.data.store import BarStore
from indtrader.data.universe import load_universe
from indtrader.scheduler.etl import fetch_universe, build_providers
from indtrader.ta import atr, rsi, sma


def _load_or_fetch(store: BarStore, symbol: str) -> pd.DataFrame:
    df = store.load(symbol)
    if df.empty:
        print(f"[cli] {symbol} not in store — fetching from provider...")
        fetch_universe([symbol], store=store, providers=build_providers(get_settings()))
        df = store.load(symbol)
    return df


def cmd_fetch(args) -> int:
    store = BarStore()
    fetch_universe([args.symbol.upper()], store=store)
    return 0


def cmd_fetch_universe(args) -> int:
    res = fetch_universe(load_universe(args.file))
    print(json.dumps(res, indent=1))
    return 0


def cmd_backtest(args) -> int:
    from indtrader.backtest.costs import DeliveryCosts
    from indtrader.backtest.engine import Backtester
    from indtrader.backtest.strategies import REGISTRY

    df = _load_or_fetch(BarStore(), args.symbol)
    if df.empty:
        print("no data", file=sys.stderr)
        return 1
    strat = REGISTRY[args.strategy]()
    bt = Backtester(costs=DeliveryCosts() if args.costs else DeliveryCosts(brokerage_rate=0.0))
    res = bt.run(df, strat, symbol=args.symbol.upper())
    bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    summary = res.summary()
    summary["buy_hold_total"] = round(float(bh), 4)
    print(json.dumps(summary, indent=1))
    return 0


def cmd_rrg(args) -> int:
    from indtrader.research.rrg import rrg_table

    store = BarStore()
    stocks = {}
    for sym in args.symbols:
        df = _load_or_fetch(store, sym)
        if not df.empty:
            stocks[sym.upper()] = df["close"]
    bench = _load_or_fetch(store, args.bench)["close"]
    weekly = {k: v.resample("W-FRI").last().dropna() for k, v in stocks.items()}
    bench_w = bench.resample("W-FRI").last().dropna()
    print(rrg_table(weekly, bench_w).to_string(index=False))
    return 0


def cmd_regime(args) -> int:
    from indtrader.research.regime import regime_from_data

    store = BarStore()
    closes = {}
    for sym in load_universe(None):
        df = _load_or_fetch(store, sym)
        if not df.empty:
            closes[sym] = df["close"]
    idx = _load_or_fetch(store, args.index)
    if not closes or idx.empty:
        print("insufficient data", file=sys.stderr)
        return 1
    r = regime_from_data(closes, idx["close"])
    print(f"regime={r.label} exposure_scale={r.exposure_scale} breadth={r.breadth} index_trend_up={r.index_trend_up}")
    return 0


def cmd_scan(args) -> int:
    from indtrader.research.regime import regime_from_data
    from indtrader.research.scanner import scan

    store = BarStore()
    closes, missing = {}, []
    for sym in load_universe(args.file):
        df = _load_or_fetch(store, sym)
        if df.empty:
            missing.append(sym)
            continue
        closes[sym] = df["close"]
    idx = _load_or_fetch(store, args.index)
    if not closes or idx.empty:
        print("insufficient data — run fetch-universe first (home network)", file=sys.stderr)
        if missing:
            print("missing:", ", ".join(missing), file=sys.stderr)
        return 1
    regime = regime_from_data(closes, idx["close"])
    table = scan(closes, bench=idx["close"], regime=regime, top=args.top)
    print(f"regime={regime.label} exposure_scale={regime.exposure_scale} "
          f"breadth={None if regime.breadth is None else round(regime.breadth, 2)}")
    print(table.to_string(index=False))
    if missing:
        print(f"[scan] no data for: {', '.join(missing)}")
    if not args.no_journal:
        from indtrader.execution.planlog import PlanLog

        log = PlanLog()
        picks = table[table["tradable"] == True].head(args.top)  # noqa: E712
        log.record_signal(
            "UNIVERSE", "scan",
            {"regime": regime.label, "exposure_scale": regime.exposure_scale,
             "picks": picks[["symbol", "score", "quadrant", "mom_6m"]].to_dict("records")},
        )
        log.close()
    return 0


def cmd_ai(args) -> int:
    from indtrader.ai.prompts import build_prompt
    from indtrader.ai.router import build_router

    df = _load_or_fetch(BarStore(), args.symbol)
    if df.empty:
        print("no data", file=sys.stderr)
        return 1
    factpack = {
        "symbol": args.symbol.upper(),
        "date": str(df.index[-1].date()),
        "close": round(float(df['close'].iloc[-1]), 2),
        "rsi14": round(float(rsi(df["close"]).iloc[-1]), 1),
        "sma50": round(float(sma(df["close"], 50).iloc[-1]), 2),
        "sma200": round(float(sma(df["close"], 200).iloc[-1]), 2),
        "ret_6m": round(float(df["close"].pct_change(126).iloc[-1]), 4),
        "atr14": round(float(atr(df).iloc[-1]), 2),
    }
    router = build_router(get_settings())
    insight = router.analyze(build_prompt(factpack))
    if insight is None:
        print("No AI provider available (deterministic factpack only):")
        print(json.dumps(factpack, indent=1))
        print("Set a key in .env and AI_PROVIDER_ORDER to enable models.")
        return 0
    print(f"[{insight.provider}] {insight.verdict} (conf {insight.confidence}) — {insight.summary}\nRisks: {insight.risks}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="indtrader", description="Personal non-intraday Indian equity platform")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch"); s.add_argument("symbol"); s.set_defaults(fn=cmd_fetch)
    s = sub.add_parser("fetch-universe"); s.add_argument("--file", default=None); s.set_defaults(fn=cmd_fetch_universe)
    s = sub.add_parser("backtest")
    s.add_argument("symbol")
    s.add_argument("--strategy", default="trend_sma200")
    s.add_argument("--costs", action="store_true", help="apply Indian delivery costs")
    s.set_defaults(fn=cmd_backtest)
    s = sub.add_parser("rrg")
    s.add_argument("symbols", nargs="+"); s.add_argument("--bench", default="NIFTY"); s.set_defaults(fn=cmd_rrg)
    s = sub.add_parser("regime"); s.add_argument("--index", default="NIFTY"); s.set_defaults(fn=cmd_regime)
    s = sub.add_parser("scan")
    s.add_argument("--file", default=None, help="universe file (default: built-in sample)")
    s.add_argument("--index", default="NIFTY")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--no-journal", action="store_true", help="don't write the shortlist to the signal journal")
    s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("ai"); s.add_argument("symbol"); s.set_defaults(fn=cmd_ai)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
