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
    from indtrader.research.regime import flip_note, regime_from_data

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
    print(flip_note(r))
    return 0


def cmd_scan(args) -> int:
    from indtrader.data import feeds
    from indtrader.research.regime import flip_note, regime_from_data
    from indtrader.research.scanner import scan

    store = BarStore()
    closes, volumes, missing, extras = {}, {}, [], {}
    for sym in load_universe(args.file):
        df = _load_or_fetch(store, sym)
        if df.empty:
            missing.append(sym)
            continue
        closes[sym] = df["close"]
        volumes[sym] = df["volume"]
        extra = {
            "deliv_per": feeds.latest_delivery_per(store, sym),
            "deliv_avg": feeds.avg_delivery_per(store, sym),
            "net_bulk": feeds.net_bulk_value(store, sym),
        }
        if any(v is not None for v in extra.values()):
            extras[sym] = extra
    idx = _load_or_fetch(store, args.index)
    if not closes or idx.empty:
        print("insufficient data — run fetch-universe first (home network)", file=sys.stderr)
        if missing:
            print("missing:", ", ".join(missing), file=sys.stderr)
        return 1
    regime = regime_from_data(closes, idx["close"])
    table = scan(closes, bench=idx["close"], regime=regime, top=args.top, extras=extras,
                 volumes=volumes)
    print(f"regime={regime.label} exposure_scale={regime.exposure_scale} "
          f"breadth={None if regime.breadth is None else round(regime.breadth, 2)}")
    print(flip_note(regime))
    print(table.to_string(index=False))
    if missing:
        print(f"[scan] no data for: {', '.join(missing)}")
    if not args.no_journal:
        from indtrader.execution.planlog import PlanLog

        log = PlanLog()
        picks = table[table["tradable"] == True].head(args.top)  # noqa: E712
        cols = [c for c in ("symbol", "score", "quadrant", "mom_6m", "deliv_per", "net_bulk",
                            "turnover_20d", "note") if c in table.columns]
        log.record_signal(
            "UNIVERSE", "scan",
            {"regime": regime.label, "exposure_scale": regime.exposure_scale,
             "picks": picks[cols].to_dict("records")},
        )
        log.close()
    return 0


def cmd_fetch_feeds(args) -> int:
    from indtrader.data import feeds

    res = feeds.fetch_feeds(lookback_days=args.days)
    print(res)
    return 0 if res["status"] == "ok" else 1


def cmd_universe(args) -> int:
    """Dump an index's constituents to a universe file (needs NseIndiaApi + network)."""
    try:
        from nse import NSE
    except ImportError:
        print("needs NseIndiaApi: pip install NseIndiaApi (works on a home network)", file=sys.stderr)
        return 1
    from indtrader.data.feeds import index_universe

    import tempfile
    from pathlib import Path

    with NSE(download_folder=tempfile.mkdtemp(prefix="nseuni_")) as nse:
        syms = index_universe(nse, index=args.index)
    if not syms:
        print(f"no constituents returned for '{args.index}'", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(syms) + "\n", encoding="utf-8")
    print(f"{len(syms)} symbols for '{args.index}' -> {out}")
    return 0


def cmd_notify(args) -> int:
    from indtrader.alerts.briefing import maybe_notify

    sent, reason = maybe_notify()
    print(f"[notify] {'sent' if sent else 'not sent'}: {reason}")
    return 0 if sent else 1


def cmd_reconcile(args) -> int:
    from indtrader.broker.fyers import FyersReadOnly
    from indtrader.broker.reconcile import reconcile
    from indtrader.execution.planlog import PlanLog

    broker = FyersReadOnly()
    if not broker.available():
        print("FYERS_ACCESS_TOKEN not set (read-only token from Fyers dashboard) — nothing to compare",
              file=sys.stderr)
        return 1
    log = PlanLog()
    journal = log.executed_positions()
    log.close()
    holdings = broker.holdings()
    rep = reconcile(journal, holdings)
    for line in rep.lines():
        print(line)
    if rep.has_issues and not args.report_only:
        from indtrader.alerts.telegram import send
        from indtrader.config import get_settings

        ok, reason = send("ind-trader reconcile\n" + "\n".join(rep.lines()), get_settings())
        print(f"[reconcile] telegram: {'sent' if ok else reason}")
    return 0


def cmd_fill(args) -> int:
    from datetime import date as _date

    from indtrader.execution.planlog import PlanLog

    when = args.date or _date.today().isoformat()
    log = PlanLog()
    fid = log.add_fill(when, args.symbol, args.side, args.qty, args.price, args.costs, args.note)
    log.close()
    print(f"ledger fill #{fid}: {args.side} {args.qty} {args.symbol.upper()} @ {args.price} on {args.date}")
    return 0


def cmd_tax(args) -> int:
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.tax import realize, tax_summary

    log = PlanLog()
    entries = log.fills()
    log.close()
    if not entries:
        print("ledger empty — record actual fills with: indtrader fill SYM BUY 100 1400 --date 2026-01-15")
        return 0
    matches = realize(entries)
    open_pos = {}
    for m_e in entries:  # reopen books just to show what's still held
        open_pos[m_e.symbol.upper()] = open_pos.get(m_e.symbol.upper(), 0) + (m_e.qty if m_e.side.upper() == "BUY" else -m_e.qty)
    print(f"{len(entries)} fills -> {len(matches)} realized lots")
    for fy in tax_summary(matches):
        for line in fy.lines():
            print(line)
    held = {s: q for s, q in sorted(open_pos.items()) if q > 0}
    print("still held:", held if held else "nothing")
    print("computation only — verify with your CA before filing.")
    return 0


def cmd_pbacktest(args) -> int:
    from indtrader.backtest.portfolio import PortfolioBacktester
    from indtrader.backtest.strategies import REGISTRY

    store = BarStore()
    symbols = args.symbols or load_universe(args.file)
    data = {}
    for sym in symbols:
        df = _load_or_fetch(store, sym)
        if not df.empty:
            data[sym] = df
    if len(data) < 2:
        print("need at least 2 symbols with data (fetch-universe on a normal network)", file=sys.stderr)
        return 1
    idx = _load_or_fetch(store, args.index)
    bt = PortfolioBacktester(capital=args.capital, max_positions=args.positions,
                             bench_closes=(idx["close"] if not idx.empty else None))
    res = bt.run(data, REGISTRY[args.strategy])
    print(json.dumps(res.summary(), indent=1))
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
    if insight.watch_for:
        print(f"Watches for: {insight.watch_for}")
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
    s = sub.add_parser("fetch-feeds"); s.add_argument("--days", type=int, default=7); s.set_defaults(fn=cmd_fetch_feeds)
    s = sub.add_parser("notify"); s.set_defaults(fn=cmd_notify)
    s = sub.add_parser("reconcile")
    s.add_argument("--report-only", action="store_true", help="print only, never send Telegram")
    s.set_defaults(fn=cmd_reconcile)
    s = sub.add_parser("fill", help="record an actual executed fill into the tax ledger")
    s.add_argument("symbol")
    s.add_argument("side", choices=["BUY", "SELL"])
    s.add_argument("qty", type=int)
    s.add_argument("price", type=float)
    s.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    s.add_argument("--costs", type=float, default=0.0, help="all-in costs of this fill (STT/stamp/GST...)")
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_fill)
    s = sub.add_parser("tax", help="FIFO realized P&L + CGT estimate per financial year")
    s.set_defaults(fn=cmd_tax)
    s = sub.add_parser("pbacktest", help="portfolio-level backtest: shared cash, concurrent positions")
    s.add_argument("symbols", nargs="*")
    s.add_argument("--file", default=None)
    s.add_argument("--index", default="NIFTY")
    s.add_argument("--strategy", default="trend_sma200")
    s.add_argument("--capital", type=float, default=1_000_000.0)
    s.add_argument("--positions", type=int, default=5)
    s.set_defaults(fn=cmd_pbacktest)
    s = sub.add_parser("universe")
    s.add_argument("--index", default="nifty 50")
    s.add_argument("--out", default="data/universe.txt")
    s.set_defaults(fn=cmd_universe)
    s = sub.add_parser("ai"); s.add_argument("symbol"); s.set_defaults(fn=cmd_ai)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
