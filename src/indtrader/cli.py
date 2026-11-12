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
from indtrader.research.splitgaps import SPLIT_JUMP  # noqa: F401 (health names it)
from indtrader.research.splitgaps import symbol_split_gaps as _symbol_split_gaps
from indtrader.scheduler.etl import fetch_universe, build_providers
from indtrader.ta import atr, rsi, sma


def _load_or_fetch(store: BarStore, symbol: str, adjusted: bool = False) -> pd.DataFrame:
    df = store.load(symbol, adjusted=adjusted)
    if df.empty:
        print(f"[cli] {symbol} not in store — fetching from provider...")
        fetch_universe([symbol], store=store, providers=build_providers(get_settings()))
        df = store.load(symbol, adjusted=adjusted)
    if adjusted and store.combined_factors(symbol).empty:
        print(f"[cli] {symbol}: no adjustment factors stored — backtest runs on RAW prices "
              "(run fetch-adj on a normal network for split/dividend-correct series)")
    return df


def cmd_fetch(args) -> int:
    store = BarStore()
    fetch_universe([args.symbol.upper()], store=store)
    return 0


def cmd_fetch_universe(args) -> int:
    res = fetch_universe(load_universe(args.file), skip_fresh=args.skip_fresh)
    print(json.dumps(res, indent=1))
    return 0


def cmd_backtest(args) -> int:
    from indtrader.backtest.costs import DeliveryCosts
    from indtrader.backtest.engine import Backtester
    from indtrader.backtest.strategies import REGISTRY

    df = _load_or_fetch(BarStore(), args.symbol, adjusted=getattr(args, "adjusted", False))
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
    r = regime_from_data(closes, idx["close"], idx["volume"])
    print(f"regime={r.label} exposure_scale={r.exposure_scale} breadth={r.breadth} index_trend_up={r.index_trend_up}")
    print(flip_note(r, r.ftd))
    return 0


def cmd_scan(args) -> int:
    from indtrader.data import feeds
    from indtrader.research import events
    from indtrader.research.regime import flip_note, regime_from_data
    from indtrader.research.scanner import scan

    store = BarStore()
    closes, volumes, bars, missing, extras = {}, {}, {}, [], {}
    for sym in load_universe(args.file):
        df = _load_or_fetch(store, sym)
        if df.empty:
            missing.append(sym)
            continue
        closes[sym] = df["close"]
        volumes[sym] = df["volume"]
        bars[sym] = df
        extra = {
            "deliv_per": feeds.latest_delivery_per(store, sym),
            "deliv_avg": feeds.avg_delivery_per(store, sym),
            "net_bulk": feeds.net_bulk_value(store, sym),
            "results_in": events.sessions_until(store, sym, args.index),
        }
        if any(v is not None for v in extra.values()):
            extras[sym] = extra
    idx = _load_or_fetch(store, args.index)
    if not closes or idx.empty:
        print("insufficient data — run fetch-universe first (home network)", file=sys.stderr)
        if missing:
            print("missing:", ", ".join(missing), file=sys.stderr)
        return 1
    # DEAD-MAN (plan §A "no fresh bars, no orders"): staleness must be measured vs
    # TODAY — the index defines its own session calendar, so bars can never be
    # "stale" against themselves. Business days; default tolerates a long weekend
    # (5) plus a couple of holidays.
    import numpy as np

    last_bar = pd.Timestamp(idx.index.max()).date()
    age_bdays = int(np.busday_count(last_bar, pd.Timestamp.today().date()))
    if age_bdays > args.max_stale and not args.ignore_stale:
        print(f"DEAD-MAN: last {args.index} bar is {last_bar} — {age_bdays} business days ago "
              f"(> {args.max_stale}). Signals on stale bars can fake a trend: fix the ETL "
              "first. --ignore-stale overrides.", file=sys.stderr)
        return 1
    regime = regime_from_data(closes, idx["close"], idx["volume"])
    table = scan(closes, bench=idx["close"], regime=regime, top=args.top, extras=extras,
                 volumes=volumes, bars=bars)
    print(f"regime={regime.label} exposure_scale={regime.exposure_scale} "
          f"breadth={None if regime.breadth is None else round(regime.breadth, 2)}")
    print(flip_note(regime, regime.ftd))
    print(table.to_string(index=False))
    if missing:
        print(f"[scan] no data for: {', '.join(missing)}")
    if not args.no_journal:
        from indtrader.execution.planlog import PlanLog

        log = PlanLog()
        picks = table[table["tradable"] == True].head(args.top)  # noqa: E712
        cols = [c for c in ("symbol", "score", "quadrant", "mom_6m", "deliv_per", "deliv_avg20",
                            "net_bulk", "turnover_20d", "base_quality", "note") if c in table.columns]
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


def cmd_fetch_adj(args) -> int:
    from indtrader.scheduler.etl import fetch_adj_factors

    syms = args.symbols or load_universe(args.file)
    res = fetch_adj_factors(syms, start=args.start)
    zero = [s for s, n in res.items() if n == 0]
    print(f"adj factors stored for {len(res) - len(zero)}/{len(res)} symbols")
    if zero:
        print("no factor data (adjusted reads stay RAW for these):", ", ".join(zero[:20]))
    return 0


def cmd_contract_test(args) -> int:
    """Phase-1 acceptance: our store's closes vs NSE's own bhavcopy for one session.
    Default session = most recent date the store holds (the bhav file for a session
    only exists after settlement — probing "today" just 403s/NSEFileUnavailable)."""
    from indtrader.data.contract import bhav_close_map, contract_check, load_bhavcopy
    from indtrader.data.nseapi_provider import NseIndiaApiProvider

    if not NseIndiaApiProvider.available():
        print("contract-test needs the nse lib (pip install 'indtrader[nse]') — run on the laptop",
              file=sys.stderr)
        return 2
    syms = load_universe(args.file) if args.file else load_universe(None)
    store = BarStore()
    try:
        from datetime import date, timedelta
        session = args.date or max(
            (str(store.last_date(s))[:10] for s in set(syms) if store.last_date(s) is not None),
            default=None)
        if not session:
            print("store empty — run fetch-universe first", file=sys.stderr)
            return 1
        # Round-4 live fact: the laptop's store now holds TODAY's patch-bar from
        # yfinance, but NSE publishes bhavcopy only after settlement — so the newest
        # store date has no bhav file and the raw NSEFileUnavailableError traceback
        # read as a crash. Step back (weekends skipped) to the newest date that HAS
        # a bhav file, printing each skip — the test should run, not die on the clock.
        n = NseIndiaApiProvider().client()
        df, attempts = None, []
        try:
            d = date.fromisoformat(session)
            for _ in range(7):
                while d.weekday() >= 5:
                    d -= timedelta(days=1)
                attempts.append(d.isoformat())
                try:
                    df = load_bhavcopy(n, d)
                    session = d.isoformat()
                    break
                except Exception as e:
                    print(f"[contract-test] no bhav file for {d} ({type(e).__name__}) "
                          f"— trying the previous session", file=sys.stderr)
                    d -= timedelta(days=1)
        finally:
            if hasattr(n, "close"):
                n.close()
        if df is None:
            print(f"no bhavcopy file for any of: {', '.join(attempts)} — NSE publish "
                  f"lag or network problem, cannot contract-check", file=sys.stderr)
            return 1
        res = contract_check(store, syms, bhav_close_map(df), session, tolerance=args.tolerance)
        print(json.dumps(res, indent=1, default=str))
        ok = res["checked"] >= args.min_checked and not res["mismatched"]
        print("PASS" if ok else f"FAIL (need >= {args.min_checked} checked, 0 mismatches)")
        return 0 if ok else 1
    finally:
        store.close()


def cmd_holidays(args) -> int:
    """NSE trading-holiday calendar (CBM) for a year — cross-check vs the store's
    NIFTY bar dates: a holiday with a bar, or a trading day missing a bar, is ETL drift."""
    from datetime import date as _date, timedelta

    from indtrader.data.contract import holidays_calendar
    from indtrader.data.nseapi_provider import NseIndiaApiProvider

    if not NseIndiaApiProvider.available():
        print("holidays needs the nse lib (pip install 'indtrader[nse]') — run on the laptop",
              file=sys.stderr)
        return 2
    year = args.year or _date.today().year
    n = NseIndiaApiProvider().client()
    try:
        hol = holidays_calendar(n, year)
    finally:
        if hasattr(n, "close"):
            n.close()
    store = BarStore()
    try:
        from indtrader.data import macrocal

        macrocal.store_cbm(store, hol)  # store the CBM view too: evidence + offline re-runs
        if getattr(args, "refresh_calendar", False):
            print(f"[holidays] holiday master: {macrocal.fetch_calendar(store)}")
        idx = store.load(args.index)
        print(f"{year}: {len(hol)} CBM trading holidays "
              f"({min(hol)} .. {max(hol)})" if hol else f"{year}: no holidays parsed?!")
        if idx.empty:
            print(f"no {args.index} bars in store — calendar stored but not cross-checked")
            return 0
        bar_days = {str(d)[:10] for d in pd.to_datetime(idx.index)}
        yr_days = {d for d in hol if d.startswith(str(year))}
        bad = sorted(d for d in yr_days if d in bar_days and _date.fromisoformat(d).weekday() < 5)
        print("holidays that HAVE index bars (real drift or special session):", bad or "none")
        n_universe = store.con.execute(
            "SELECT count(DISTINCT symbol) FROM candles WHERE tf='daily'").fetchone()[0]
        if bad:
            # Classify by SHARE of the universe trading that day — absolute counts
            # mislead (round 4: "494 symbols" was called leakage; with a 500-symbol
            # universe that is market-wide -> the calendar row is the suspect).
            ph = ",".join("?" * len(bad))
            counts = dict(store.con.execute(
                f"SELECT d, count(DISTINCT symbol) FROM candles WHERE d IN ({ph}) AND tf='daily' "
                f"GROUP BY d", bad).fetchall())
            for d in bad:
                c = counts.get(d, 0)
                share = c / n_universe if n_universe else 0.0
                if share >= 0.5:
                    # Market-wide: the WHOLE day's series is suspect, not just the bar.
                    # Off-by-one dates (UTC shift) leave a signature: the date's span
                    # looks like a trading day AND the PRIOR trading day is missing.
                    sp = store.con.execute(
                        "SELECT count(DISTINCT close) FROM candles WHERE d=? AND tf='daily'",
                        (d,)).fetchone()
                    n_distinct_prices = sp[0] or 0
                    prev = (_date.fromisoformat(d) - timedelta(days=1))
                    while prev.weekday() >= 5:
                        prev -= timedelta(days=1)
                    prev_missing = prev.isoformat() not in bar_days
                    print(f"  {d}: {c}/{n_universe} symbols ({share:.1%}) -> MARKET-WIDE: "
                          f"calendar row suspect or the market actually traded")
                    print(f"      span: {n_distinct_prices} distinct closes "
                          f"(0 = flat/flagged rows; hundreds = real session), "
                          f"prev trading day {prev} missing from index: {prev_missing} "
                          f"(True True = off-by-one signature)")
                elif share >= 0.05:
                    print(f"  {d}: {c}/{n_universe} symbols ({share:.1%}) -> ambiguous, "
                          f"run the span query before concluding")
                else:
                    print(f"  {d}: {c}/{n_universe} symbols ({share:.1%}) -> few symbols: "
                          f"yfinance feed leakage (isolated bars on a closed day)")
        # also report holiday dates that have stock bars but NO index bar
        strays = sorted((d for d in yr_days
                         if d not in bar_days and _date.fromisoformat(d).weekday() < 5))
        if strays:
            ph = ",".join("?" * len(strays))
            hits = store.con.execute(
                f"SELECT d, count(DISTINCT symbol) FROM candles WHERE d IN ({ph}) AND tf='daily' "
                f"GROUP BY d", strays).fetchall()
            if hits:
                print("holiday with STOCK bars but no index bar (contaminated):",
                      ", ".join(f"{d} [{c} symbols]" for d, c in hits))
        # Holiday master in the store (round-5 disputed dates): the drift ledger,
        # re-computable OFFLINE. Same-source caveat is in macrocal's docstring —
        # this is the exchange's words kept as evidence, not a second opinion.
        cov = macrocal.calendar_status(store)
        if not cov:
            print("holiday master not stored — run `holidays --refresh-calendar` to keep "
                  "the exchange list in the store for offline re-checks")
        else:
            disp = macrocal.disputed_dates(store)
            print(f"holiday master stored {cov[0]}..{cov[1]} — exchange-holiday dates that "
                  f"have a NIFTY bar (drift ledger): {disp or 'none'}")
        return 0
    finally:
        store.close()


# _symbol_split_gaps: imported above from indtrader.research.splitgaps — the ONE
# implementation shared by `health`, `coractions --gap-list` and the dashboard.


def cmd_health(args) -> int:
    """Data-health strip: rows, span, missing sessions vs the index calendar, staleness,
    adjustment coverage, split-gap. Exits 1 when any symbol crosses the thresholds — a
    missing candle can fake a trend, so this is a gate, not a report. The split-gap
    check is a loud print, not a gate: round-4 proved adj_coverage=100% can coexist
    with an unadjusted 50% split cliff (dividend-only factors), so 'covered' must
    mean 'covers every big raw move', not 'some factors stored'."""
    from indtrader.research.calendar import index_sessions, missing_sessions, staleness_days

    store = BarStore()
    try:
        idx = store.load(args.index)
        if idx.empty:
            print(f"health needs '{args.index}' bars in store first (run fetch-universe on a normal network)",
                  file=sys.stderr)
            return 1
        sessions = index_sessions(idx)
        rows, bad, split_gaps = [], 0, []
        for sym in sorted(set(load_universe(args.file))):
            df = store.load(sym, validate=False)
            if df.empty:
                rows.append({"symbol": sym, "rows": 0, "first": None, "last": None,
                             "missing_sessions": None, "stale_sessions_ago": None, "adj_coverage": None})
                bad += 1
                continue
            miss = missing_sessions(df.index, sessions)
            stale = staleness_days(df.index, sessions)
            f = store.adj_factors(sym)
            cov = round(float(f.reindex(df.index, method="ffill").ffill().notna().mean()), 3) if len(f) else 0.0
            # Split-gap runs against the MERGED factors (yf + recorded NSE splits):
            # the question is "is the adjusted series wrong", not "does yf alone
            # cover it" — a symbol whose split was recorded must go quiet here,
            # and one still named has a cliff NO source explains.
            gaps = _symbol_split_gaps(store, sym, df, SPLIT_JUMP)
            if gaps:
                split_gaps.append(f"{sym}: " + "; ".join(gaps[:4]) + (" ..." if len(gaps) > 4 else ""))
            rows.append({"symbol": sym, "rows": len(df), "first": str(df.index[0].date()),
                         "last": str(df.index[-1].date()), "missing_sessions": int(len(miss)),
                         "stale_sessions_ago": stale, "adj_coverage": cov})
            if len(miss) > args.max_missing or (stale if stale is not None else 10**9) > args.max_stale:
                bad += 1
        print(pd.DataFrame(rows).to_string(index=False))
        if split_gaps:
            print(f"[health] SPLIT-GAP: {len(split_gaps)} symbol(s) hold big raw cliffs the "
                  f"factors do NOT cover — adjusted series is wrong for these:")
            for g in split_gaps[:args.max_gaps]:
                print("  " + g)
            print("  (step is measured against MERGED yfinance+recorded-split factors — a gap "
                  "here means NO source explains the day; the step column shows what the "
                  "factors actually did — a split needs 0.4-0.7 or 1.4-2.5)")
        print(f"[health] {bad} problem symbol(s) of {len(rows)} "
              f"(thresholds: missing>{args.max_missing} sessions, stale>{args.max_stale})")
        return 1 if bad else 0
    finally:
        store.close()


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


def cmd_mf_navs(args) -> int:
    from indtrader.portfolio import mf

    store = BarStore()
    try:
        out = mf.fetch_navs(store)
        if out.get("ok"):
            print(f"stored {out['stored']} NAVs dated {out['nav_date']} "
                  f"({out['dropped_malformed']} malformed lines dropped)")
            return 0
        print(f"mf-navs failed: {out['error']}", file=sys.stderr)
        return 1
    finally:
        store.close()


def cmd_mf(args) -> int:
    """No args: MF slice of the book at stored NAVs. With text: scheme search."""
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio import mf
    from indtrader.portfolio.valuation import held_quantities, resolve_marks

    store, log = BarStore(), PlanLog()
    try:
        if args.query:
            hits = mf.resolve_scheme(store, args.query[0])
            if not hits:
                print("no stored scheme matches — run mf-navs first (needs network), or refine the text")
                return 1
            for code, name in hits:
                print(f"MF:{code}  {name}")
            print("record instalments with: fill-mf <code> BUY <units> <nav>")
            return 0
        fills = [f for f in log.fills() if mf.is_mf(f.symbol)]
        if not fills:
            print("no MF flows yet — record SIP instalments with: fill-mf <scheme-code> BUY <units> <nav>")
            return 0
        held = held_quantities(fills)
        marks = resolve_marks(store, held)
        rows = []
        for sym, q in sorted(held.items()):
            nav = marks.get(sym)
            rows.append({"symbol": sym, "units": round(q, 4), "nav": nav,
                         "value": round(q * nav, 2) if nav else None})
        print(pd.DataFrame(rows).to_string(index=False))
        missing = [s for s, v in marks.items() if v is None]
        if missing:
            print("NAV missing (run mf-navs):", ", ".join(missing))
        return 0
    finally:
        store.close()
        log.close()


def cmd_fill_mf(args) -> int:
    """One SIP instalment / redemption: fractional units, NAV from statement or AMFI."""
    from datetime import date as _date

    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio import mf

    side = args.side.upper()
    if side not in ("BUY", "SELL"):
        print("side must be BUY or SELL", file=sys.stderr)
        return 1
    if args.units <= 0 or args.nav <= 0 or args.costs < 0:
        print("units and nav must be positive, costs non-negative", file=sys.stderr)
        return 1
    dt = _date.fromisoformat(args.date) if args.date else _date.today()
    log = PlanLog()
    try:
        code = args.symbol if str(args.symbol).isdigit() else str(args.symbol)
        fid = log.add_fill(dt, mf.MF_PREFIX + code, side, args.units, args.nav,
                           args.costs, args.note or ("sip via cli" if side == "BUY" else "redeem via cli"))
        print(f"recorded fill #{fid}: {side} {args.units} units MF:{code} @ NAV {args.nav:g} on {dt}")
        return 0
    finally:
        log.close()


def cmd_sips(args) -> int:
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio import mf
    from indtrader.portfolio.valuation import book_xirr, resolve_marks

    store, log = BarStore(), PlanLog()
    try:
        flows = mf.mf_flows(log.fills())
        if not flows:
            print("no SIP flows yet — record instalments with fill-mf (or via the /fill bot command)")
            return 0
        agg: dict[str, dict] = {}
        for f in flows:
            a = agg.setdefault(f.symbol, {"units": 0.0, "invested": 0.0, "redeemed": 0.0, "n": 0})
            a["n"] += 1
            if f.side.upper() == "BUY":
                a["units"] += f.qty
                a["invested"] += f.qty * f.price + f.costs
            else:
                a["units"] -= f.qty
                a["redeemed"] += f.qty * f.price - f.costs
        marks = resolve_marks(store, agg)
        rows = []
        for sym, a in sorted(agg.items()):
            nav = marks.get(sym)
            rows.append({"symbol": sym, "fills": a["n"], "units": round(a["units"], 3),
                         "invested": round(a["invested"], 0), "redeemed": round(a["redeemed"], 0),
                         "value": round(a["units"] * nav, 0) if nav else None,
                         "gain%": (round((a["redeemed"] + (a["units"] * nav)) / a["invested"] - 1, 4)
                                   if nav and a["invested"] else None)})
        print(pd.DataFrame(rows).to_string(index=False))
        out = book_xirr(flows, marks)
        print(json.dumps(out, indent=1))
        return 0 if out["ok"] else 1
    finally:
        store.close()
        log.close()


def cmd_rebalance(args) -> int:
    import json as _json
    from pathlib import Path

    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.rebalance import rebalance_plan
    from indtrader.portfolio.valuation import held_quantities, resolve_marks

    try:
        targets = _json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"targets file unreadable ({args.file}): {e}", file=sys.stderr)
        return 1
    if not isinstance(targets, dict) or not targets:
        print("targets file must be a JSON object {symbol: weight}", file=sys.stderr)
        return 1
    try:
        tw = {str(k).upper(): float(v) for k, v in targets.items()}
    except (TypeError, ValueError):
        print("every target weight must be a number", file=sys.stderr)
        return 1
    if any(v < 0 or v > 1 for v in tw.values()) or sum(tw.values()) > 1.0 + 1e-9:
        print(f"weights must be in [0,1] and sum <= 1 (residual = cash); got sum={sum(tw.values()):.3f} — refusing",
              file=sys.stderr)
        return 1
    store, log = BarStore(), PlanLog()
    try:
        held = held_quantities(log.fills())
        marks = resolve_marks(store, sorted(set(held) | set(tw)))
        closes = {s: store.load(s)["close"].tolist() for s in held if not mf_is(s) and not store.load(s).empty}
        plan = rebalance_plan(held, marks, tw, closes=closes, tol=args.tol)
        if not plan:
            print("book is within tolerance of every target — nothing to do")
        for t in plan:
            px = "unknown" if t.price is None else f"{t.price:,.2f}"
            qty = "-" if t.qty == 0 else f"{t.qty:,.4f}"
            print(f"{t.action:5s} {t.symbol:24s} qty {qty:>12s} @ {px:>10s} "
                  f"~{t.est_value:,.0f}  {t.note}")
        print("proposals only — human-confirmed execution is the standing rule")
        return 0
    finally:
        store.close()
        log.close()


def mf_is(sym: str) -> bool:
    from indtrader.portfolio.mf import is_mf

    return is_mf(sym)


def cmd_action(args) -> int:
    """Record a corporate action EXACTLY as the statement showed it — the engine
    never infers these from price jumps."""
    from datetime import date as _date

    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.actions import add_action

    dt = _date.fromisoformat(args.date) if args.date else _date.today()
    log = PlanLog()
    try:
        aid = add_action(log, dt, args.symbol, args.kind, qty_delta=args.qty,
                         cash=args.cash, tds=args.tds, note=args.note or "")
        print(f"recorded action #{aid}: {args.kind.lower()} {args.symbol.upper()} on {dt}"
              + (f" qty {args.qty:g}" if args.qty else "")
              + (f" cash {args.cash:g}" if args.cash else "")
              + (f" tds {args.tds:g}" if args.tds else ""))
        return 0
    except ValueError as e:
        print(f"action rejected: {e}", file=sys.stderr)
        return 1
    finally:
        log.close()


def cmd_actions(args) -> int:
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.actions import load_actions

    log = PlanLog()
    try:
        rows = load_actions(log)
        if not rows:
            print("no corporate actions recorded — add statement facts with: "
                  "action SYM dividend --cash 4000 --tds 200 --date 2026-08-01")
            return 0
        for a in rows[-40:]:
            print(f"{a.dt}  {a.symbol:14s} {a.kind:10s} qty {a.qty_delta:>10,.3f} "
                  f"cash {a.cash:>12,.2f} tds {a.tds:>9,.2f}  {a.note[:40]}")
        return 0
    finally:
        log.close()


def cmd_harvest(args) -> int:
    """Pre-March harvest candidates: open FIFO lots x marks x this FY's realized LTCG."""
    from datetime import date as _date

    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.actions import apply_actions, load_actions
    from indtrader.portfolio.harvest import harvest_candidates, lines
    from indtrader.portfolio.tax import realize
    from indtrader.portfolio.valuation import held_quantities, resolve_marks

    store, log = BarStore(), PlanLog()
    try:
        entries = apply_actions(log.fills(), load_actions(log))
        equity = [e for e in entries if not e.symbol.upper().startswith("MF:")]
        mf_n = len(entries) - len(equity)
        if mf_n:
            print(f"(skipping {mf_n} MF ledger rows: debt-fund slab rules differ; equity lots only)")
        if not equity:
            print("no equity fills — nothing to harvest")
            return 0
        marks = resolve_marks(store, held_quantities(equity))
        asof = _date.fromisoformat(args.asof) if args.asof else _date.today()
        rep = harvest_candidates(equity, marks, realize(equity), asof)
        for line in lines(rep):
            print(line)
        return 0
    finally:
        store.close()
        log.close()


def cmd_decay(args) -> int:
    """Paper book vs the strategy's own Monte-Carlo band. REVIEW flag = human decides;
    nothing here acts."""
    from indtrader.backtest.costs import DeliveryCosts
    from indtrader.backtest.engine import Backtester
    from indtrader.backtest.strategies import REGISTRY
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.actions import apply_actions, load_actions
    from indtrader.portfolio.tax import realize
    from indtrader.research.decay import decay_check

    store, log = BarStore(), PlanLog()
    try:
        df = _load_or_fetch(store, args.symbol)
        if df.empty:
            print(f"no bars for {args.symbol}", file=sys.stderr)
            return 1
        res = Backtester(costs=DeliveryCosts()).run(df, REGISTRY[args.strategy](), symbol=args.symbol.upper())
        baseline = [t.pnl for t in res.trades if t.exit_price is not None]
        paper = [m.net for m in realize(apply_actions(log.fills(), load_actions(log)))]
        out = decay_check(baseline, paper, capital=args.capital, window=args.window)
        if out is None:
            print(f"cannot judge: baseline has {len(baseline)} closed trades "
                  f"(< 20 — too little to bootstrap honestly)", file=sys.stderr)
            return 1
        print(json.dumps(out, indent=1))
        if out.get("below_p5_all"):
            print("-> strategy is below its OWN expectation band: put it under review "
                  "(review = decide, not auto-trade).")
        return 0
    finally:
        store.close()
        log.close()


def cmd_import_csv(args) -> int:
    from indtrader.data.ingest import FORMATS, import_into_log, parse_trade_csv
    from indtrader.execution.planlog import PlanLog

    col_map = None
    if args.map:
        col_map = {}
        for pair in args.map.split(","):
            field, _, col = pair.partition("=")
            col_map[field.strip()] = col.strip()
    try:
        entries = parse_trade_csv(args.file, fmt=args.format, column_map=col_map)
    except ValueError as e:
        print(f"import rejected: {e}", file=sys.stderr)
        return 1
    if not args.apply:
        print(f"{len(entries)} rows validated; first 5:")
        for e in entries[:5]:
            print(f"  {e.dt} {e.symbol} {e.side} {e.qty:g} @ {e.price:,.2f} costs {e.costs:,.2f}")
        print("(nothing written — this CLI never imports on faith: rerun with --apply)")
        return 0
    log = PlanLog()
    try:
        n = import_into_log(entries, log)
        print(f"imported {n} fills ({len(entries) - n} exact duplicates skipped)")
        return 0
    finally:
        log.close()


def cmd_shareholding(args) -> int:
    from indtrader.research import shareholding as sh

    store = BarStore()
    try:
        if args.set is not None:
            try:
                sym_q, promoter, fii, dii, public, pledge = args.set.split(":")
                sym, period = sym_q.rsplit("@", 1)

                def f(v):
                    return None if v in ("", "-") else float(v)

                sh.record(store, sym, period, f(promoter), f(fii), f(dii), f(public), f(pledge))
                print(f"recorded {sym.upper()} {period}")
            except ValueError as e:
                print(f"bad --set (want SYM@PERIOD:promoter:fii:dii:public:pledge): {e}",
                      file=sys.stderr)
                return 1
            return 0
        if args.fetch:
            print(json.dumps(sh.try_fetch(store, args.fetch), indent=1))
            return 0
        if args.symbol:
            dl = sh.deltas(store, args.symbol)
            if not dl:
                print(f"no quarters stored for {args.symbol.upper()} — record from the "
                      "exchange filing with --set, or probe the live shape with --fetch")
                return 0
            for d in dl:
                mark = "  <-- " + ", ".join(d["flags"]) if d["flags"] else ""
                print(f"{d['prev']} -> {d['period']}: promoter {d['dpromoter']}, "
                      f"fii {d['dfii']}, pledge {d['dpledge']}{mark}")
            return 0
        alerts = sh.holding_alerts(store, [s.strip().upper() for s in args.holdings.split(",")
                                           if s.strip()] if args.holdings else [])
        print("\n".join(alerts) if alerts else "no shareholding red deltas on holdings")
        return 0
    finally:
        store.close()


def cmd_coractions(args) -> int:
    """Corporate actions, five modes: probe (EVERY corporate-actions-ish
    method/arg-form, shape inventory — round-5's single empty payload proved
    nothing); --from-raw offline replay that BUILDS factors from stored
    payloads; --set for one human-confirmed event when NSE's text states no
    ratio; --resolve offline batch re-parse of ratio-NULL split-like rows with
    the CURRENT parser; and --gap-list to batch-probe every symbol health
    would name. Parsing stays confirmed-shape-only in every path."""
    from pathlib import Path

    from indtrader.data.universe import load_universe
    from indtrader.research import coractions as ca

    store = BarStore()
    try:
        if args.set_event is not None:
            if not args.symbol:
                print("coractions --set needs SYMBOL", file=sys.stderr)
                return 2
            try:
                print(ca.record_manual_event(store, args.symbol, args.set_event))
                return 0
            except ValueError as e:
                print(f"bad corporate action: {e}", file=sys.stderr)
                return 2

        if args.resolve:
            rows = ca.resolve_unresolved(store)
            done = [r for r in rows if r["ratio"] is not None]
            for r in done:
                print(f"RESOLVED  {r['symbol']:<12} {r['date']}  ratio {r['ratio']}  [{r['kind'][:60]}]")
            for r in rows:
                if r["ratio"] is None:
                    print(f"UNRESOLVED {r['symbol']:<12} {r['date']}  [{r['kind'][:70]}]")
                    print(f"           -> read the exchange record, then: {r['set']}")
            print(f"coractions --resolve: {len(done)}/{len(rows)} stored-text rows resolved "
                  "by the CURRENT parser; the rest need human --set (never a guessed ratio)")
            return 0

        if args.gap_list:
            from indtrader.data.nseapi_provider import NseIndiaApiProvider

            if not NseIndiaApiProvider.available():
                print("coractions --gap-list needs the nse lib (pip install 'indtrader[nse]') "
                      "— run on the laptop", file=sys.stderr)
                return 2
            flagged = []
            for s in load_universe(Path(args.file) if args.file else None):
                df = store.load(s, validate=False)
                if not df.empty and _symbol_split_gaps(store, s, df, 0.35):
                    flagged.append(s)
            if args.limit:
                flagged = flagged[:args.limit]
            print(f"[coractions] split-gap symbols: {len(flagged)} — probing each once "
                  "(N in a line = factors stored, U = unresolved ratio-less events)")
            got_any = 0
            for s in flagged:
                out = ca.try_fetch(store, s)
                marks = [x for x in out.get("probed", []) if "split factors" in x or "UNRESOLVED" in x]
                tail = marks[-1].split(": ", 1)[1] if marks else (out.get("error") or "no parseable payload")
                print(f"  {s}: {tail[:140]}")
                if "split factors" in tail and " 0 split factors" not in tail:
                    got_any += 1
            print(f"[coractions] factors or events landed for {got_any}/{len(flagged)}; "
                  "re-run health to see the SPLIT-GAP list shrink")
            return 0

        if args.from_raw:
            if not args.symbol:
                print("--from-raw needs SYMBOL", file=sys.stderr)
                return 2
            outs = []
            for fetched_on, method, payload in ca.raw_payloads(store, args.symbol):
                try:
                    events = ca.parse_events(args.symbol, payload)
                    note = ca.record_parsed(store, args.symbol, events)  # replay BUILDS factors
                except (KeyError, ValueError) as e:
                    note = f"parse REFUSED: {e}"
                outs.append({"fetched_on": fetched_on, "method": method, "result": note})
            if not outs:
                print(f"no stored corporate-actions payloads for {args.symbol.upper()} — "
                      f"run the probe first (no --from-raw)", file=sys.stderr)
                return 1
            print(json.dumps(outs, indent=1, default=str))
            return 0

        if not args.symbol:
            print("coractions needs SYMBOL (probe/--from-raw/--set) or --gap-list", file=sys.stderr)
            return 2
        from indtrader.data.nseapi_provider import NseIndiaApiProvider

        if not NseIndiaApiProvider.available():
            print("coractions needs the nse lib (pip install 'indtrader[nse]') — run on the laptop",
                  file=sys.stderr)
            return 2
        out = ca.try_fetch(store, args.symbol)
        print(json.dumps(out, indent=1, default=str))
        return 0 if out["ok"] else 1
    finally:
        store.close()


def cmd_backup(args) -> int:
    from indtrader.scheduler.backup import backup_all, prune

    s = get_settings()
    dbs = [s.data_dir / "bars.sqlite", s.data_dir / "plans.sqlite"]
    dest = args.dest or (s.data_dir / "backups")
    res = backup_all(dbs, dest)
    removed = prune(dest, keep=args.keep)
    print(json.dumps({**res, "pruned": removed}, indent=1))
    if res["failed"]:
        print("BACKUP PROBLEM — fix before trusting anything", file=sys.stderr)
        return 1
    return 0


def cmd_restore(args) -> int:
    from pathlib import Path

    from indtrader.scheduler.backup import restore

    s = get_settings()
    target = {"bars": s.data_dir / "bars.sqlite", "plans": s.data_dir / "plans.sqlite"}[args.kind]
    try:
        out = restore(Path(args.backup), target)
        print(f"restored {args.backup} -> {out} (previous copy kept as {target.name}.pre-restore)")
        return 0
    except Exception as e:
        print(f"restore refused: {e}", file=sys.stderr)
        return 1


def cmd_dashboard(args) -> int:
    from indtrader.web.dashboard import serve

    return serve(host=args.host, port=args.port, universe_file=args.universe)


def cmd_bot(args) -> int:
    from indtrader.alerts.commands import poll_loop

    return poll_loop()


def cmd_pause(args) -> int:
    from indtrader.alerts.pause import flag_path, set_paused

    set_paused(True, "paused via CLI")
    print(f"daily outbound loop paused ({flag_path()}) — resume with: indtrader resume")
    return 0


def cmd_resume(args) -> int:
    from indtrader.alerts.pause import is_paused, set_paused

    set_paused(False)
    print("loop active" if not is_paused() else "resume FAILED — flag still present")
    return 0 if not is_paused() else 1


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
    note = args.note or ""
    log = PlanLog()
    plan_row = None
    if args.plan:
        plan_row = log.con.execute(
            "SELECT symbol, status, entry, qty FROM plans WHERE id=?", [args.plan]).fetchone()
        if plan_row is None:
            print(f"no plan #{args.plan} — create one first: indtrader plan SYM BUY ...",
                  file=sys.stderr)
            log.close()
            return 1
        if plan_row[1] != "open":
            print(f"plan #{args.plan} is '{plan_row[1]}', not open — fills only "
                  "close OPEN plans", file=sys.stderr)
            log.close()
            return 1
    fid = log.add_fill(when, args.symbol, args.side, args.qty, args.price, args.costs,
                       ((note + " ") if note else "") + (f"plan#{args.plan}" if args.plan else note))
    if args.plan:
        log.mark(args.plan, "executed")
    log.close()
    slip = ""
    if plan_row is not None:
        p_entry, p_qty = float(plan_row[2]), float(plan_row[3])
        sl = (args.price - p_entry) / p_entry if p_entry else 0.0
        slip = (f" — slippage vs plan entry {p_entry}: {sl:+.2%} on this fill"
                + ("" if args.qty == p_qty else f" (planned qty {p_qty:g}, filled {args.qty:g})"))
    print(f"ledger fill #{fid}: {args.side} {args.qty} {args.symbol.upper()} @ {args.price} on {when}"
          + (f" (plan #{args.plan} -> executed{slip})" if args.plan else ""))
    return 0


def cmd_plan(args) -> int:
    """The plan-vs-actual front door: every position starts as a WRITTEN plan
    (Phase-4 rule: signal traceable to a rationale)."""
    from datetime import date as _date

    from indtrader.execution.planlog import PlanLog
    from indtrader.models import OrderPlan

    reason = (args.reason or "").strip()
    if len(reason) < 10:
        print("a written rationale is mandatory (>= 10 chars) — no reason, no plan",
              file=sys.stderr)
        return 1
    if not 0 < args.stop < args.entry < args.target:
        print(f"for a BUY need stop < entry < target (got {args.stop} / {args.entry} / {args.target})",
              file=sys.stderr)
        return 1
    log = PlanLog()
    pid = log.add_plan(OrderPlan(symbol=args.symbol, action="BUY", qty=args.qty, entry=args.entry,
                                 stop=args.stop, target=args.target, reason=reason,
                                 setup=args.setup or "manual"))
    log.expire_stale(_date.today())
    log.close()
    print(f"plan #{pid}: BUY {args.qty} {args.symbol.upper()} entry {args.entry} "
          f"stop {args.stop} target {args.target} [{args.setup or 'manual'}] — {reason}")
    return 0


def cmd_plans(args) -> int:
    from indtrader.execution.planlog import PlanLog

    log = PlanLog()
    rows = log.con.execute(
        "SELECT id, created, symbol, qty, entry, stop, target, setup, status, reason "
        "FROM plans " + ("" if args.all else "WHERE status='open' ") +
        "ORDER BY status DESC, created DESC LIMIT 40").fetchall()
    log.close()
    if not rows:
        print("no open plans — draft one with: indtrader plan SYM BUY --qty N --entry E "
              "--stop S --target T --reason \"...\"")
        return 0
    for r in rows:
        print(f"#{r[0]:<4} {str(r[1])[:16]} {r[2]:<12} qty {r[3]:<6} {r[4]}/{r[5]}/{r[6]} "
              f"[{r[7]}] {r[8]:<9} {str(r[9])[:60]}")
    return 0


def cmd_plan_mark(args) -> int:
    from indtrader.execution.planlog import PlanLog

    if args.status not in ("executed", "cancelled", "stale"):
        print("status must be executed|cancelled|stale (an executed BUY normally arrives "
              "via: fill ... --plan ID)", file=sys.stderr)
        return 1
    log = PlanLog()
    row = log.con.execute("SELECT id, symbol, status FROM plans WHERE id=?", [args.id]).fetchone()
    if row is None:
        print(f"no plan #{args.id}", file=sys.stderr)
        log.close()
        return 1
    log.mark(args.id, args.status)
    log.close()
    print(f"plan #{args.id} ({row[1]}): {row[2]} -> {args.status}")
    return 0


def cmd_attribution(args) -> int:
    """Plan §D monthly attribution: realized P&L by SETUP x index-trend-at-entry.
    Lot->plan link: most recent executed plan for the symbol created on/before the
    lot's buy date; no plan = 'unplanned' (visible, not hidden)."""
    from datetime import datetime as _dt

    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.actions import apply_actions, load_actions
    from indtrader.portfolio.tax import realize

    store = BarStore()
    log = PlanLog()
    try:
        entries = [e for e in apply_actions(log.fills(), load_actions(log))
                   if not e.symbol.upper().startswith("MF:")]
        if not entries:
            print("no fills yet")
            return 0
        idx = store.load(args.index)
        closes = pd.to_datetime(idx.index).date if not idx.empty else []
        s = idx["close"].astype(float) if not idx.empty else None
        sma_ok = s is not None and len(s) >= 50
        sma_vals = s.rolling(200, min_periods=50).mean() if sma_ok else None

        def trend(d):
            if not sma_ok:
                return "?"
            days = list(closes)
            prior = [x for x in days if x <= d]
            if not prior:
                return "?"
            i = days.index(max(prior))
            v, c = sma_vals.iloc[i], float(s.iloc[i])
            if pd.isna(v):
                return "?"
            return "up" if c > v else "down"

        plans = log.con.execute(
            "SELECT created, symbol, setup FROM plans WHERE status='executed' ORDER BY created").fetchall()
        agg = {}
        for m in realize(entries):
            setup = "unplanned"
            for created, psym, psetup in plans:
                if psym == m.symbol and _dt.strptime(str(created)[:10], "%Y-%m-%d").date() <= m.buy_dt:
                    setup = psetup or "manual"
            tag = (setup, trend(m.buy_dt))
            r = agg.setdefault(tag, {"n": 0, "wins": 0, "net": 0.0})
            r["n"] += 1
            r["wins"] += 1 if m.net > 0 else 0
            r["net"] += m.net
        if not agg:
            print("no realized lots yet")
            return 0
        rows = [{"setup": k[0], "trend_at_entry": k[1], "lots": v["n"],
                 "win_rate": round(v["wins"] / v["n"], 2), "net": round(v["net"], 0)}
                for k, v in sorted(agg.items())]
        print(pd.DataFrame(rows).to_string(index=False))
        print("retro-tagged from plans+bars (trend = index vs its 200-DMA at the lot's buy "
              "date); unplanned = fill without an executed plan.")
        return 0
    finally:
        store.close()
        log.close()


def cmd_compare(args) -> int:
    """Plan §D A/B compare: same symbol, window, costs — every strategy side by side."""
    from indtrader.backtest.costs import DeliveryCosts
    from indtrader.backtest.engine import Backtester
    from indtrader.backtest.strategies import REGISTRY

    df = _load_or_fetch(BarStore(), args.symbol, adjusted=args.adjusted)
    if df.empty:
        print("no data", file=sys.stderr)
        return 1
    names = ([s.strip() for s in args.strategies.split(",")] if args.strategies
             else sorted(REGISTRY))
    rows = []
    gross = DeliveryCosts(stt_buy=0.0, stt_sell=0.0, exchange_txn=0.0, sebi=0.0,
                          stamp_duty_buy=0.0, cgst_on_charges=0.0, slbm_buy=0.0, slippage=0.0)
    for n in names:
        if n not in REGISTRY:
            print(f"unknown strategy '{n}' (have: {', '.join(sorted(REGISTRY))})", file=sys.stderr)
            return 1
        res = Backtester(costs=DeliveryCosts() if args.costs else gross) \
            .run(df, REGISTRY[n](), symbol=args.symbol.upper())
        sm = res.summary()
        rows.append({"strategy": n, "trades": sm.get("trades"), "cagr": sm.get("cagr"),
                     "sharpe": sm.get("sharpe"), "maxdd": sm.get("max_drawdown"),
                     "win_rate": sm.get("win_rate"), "profit_factor": sm.get("profit_factor")})
    bh = float(df["close"].iloc[-1] / df["close"].iloc[0] - 1)
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"(buy&hold total {bh:+.1%} — a strategy must beat this AFTER costs to deserve capital)")
    return 0


def cmd_tax(args) -> int:
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.actions import apply_actions, dividend_income_by_fy, load_actions
    from indtrader.portfolio.tax import realize, tax_summary, tax_summary_with_setoff

    log = PlanLog()
    actions = load_actions(log)
    entries = apply_actions(log.fills(), actions)
    log.close()
    dividends = dividend_income_by_fy(actions)
    if not entries:
        print("ledger empty — record actual fills with: indtrader fill SYM BUY 100 1400 --date 2026-01-15")
        return 0
    matches = realize(entries)
    open_pos = {}
    for m_e in entries:  # reopen books just to show what's still held
        open_pos[m_e.symbol.upper()] = open_pos.get(m_e.symbol.upper(), 0) + (m_e.qty if m_e.side.upper() == "BUY" else -m_e.qty)
    print(f"{len(entries)} fills -> {len(matches)} realized lots")
    if args.setoff:
        for t in tax_summary_with_setoff(matches):
            print(f"{t.financial_year}: net STCG {t.stcg_net:,.0f} | net LTCG {t.ltcg_net:,.0f}"
                  f" | brought losses used: STCG {t.used_brought_stcl:,.0f} LTCG {t.used_brought_ltlg:,.0f}"
                  f" -> taxable {t.taxable_stcg:,.0f} + {t.taxable_ltcg:,.0f}"
                  f" (exemption used {t.exemption_used:,.0f}) = tax {t.tax_total:,.0f}")
            if t.carry_stcg_out or t.carry_ltcg_out:
                print(f"   carry forward: STCG {t.carry_stcg_out:,.0f} LTCG {t.carry_ltcg_out:,.0f}"
                      + (f" | {t.note}" if t.note else ""))
        print("(set-off order + 8-year expiry per documented simplifications; ITR-order approximation)")
        for fy, d in dividends.items():
            print(f"{fy}: dividend income gross {d['gross']:,.0f} - TDS {d['tds']:,.0f} = net "
                  f"{d['net']:,.0f}  [slab-rate income; reported, not computed]")
    else:
        for fy in tax_summary(matches):
            for line in fy.lines(dividends.get(fy.financial_year)):
                print(line)
    held = {s: (round(q, 4) if q == q else q) for s, q in sorted(open_pos.items()) if q > 0}
    print("still held:", held if held else "nothing")
    print("computation only — verify with your CA before filing.")
    return 0


def _held_symbols() -> list[str]:
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.valuation import held_quantities

    log = PlanLog()
    try:
        return sorted(held_quantities(log.fills()))
    finally:
        log.close()


def cmd_ann(args) -> int:
    """Announcement triage for the holdings only. Fetches when a live NSE lib exists;
    triages what's stored either way; alerts over Telegram when configured and asked."""
    from indtrader.data import announce
    from indtrader.data.store import BarStore

    store = BarStore()
    try:
        if not args.no_fetch:
            res = announce.fetch_announcements(_held_symbols(), store)
            print(f"[ann] fetch: {res}")
        matches = announce.triaged_recent(store, _held_symbols(), days=args.days)
        text = announce.format_alert(matches)
        print(text)
        if args.telegram:
            from indtrader.alerts.telegram import send
            from indtrader.config import get_settings
            s = get_settings()
            if s.telegram_bot_token.strip() and s.telegram_chat_id.strip() and matches:
                ok, why = send(text, s)
                print(f"[ann] telegram {'sent' if ok else 'not sent'}: {why} "
                      f"({sum(1 for m in matches if m.severity == 'RED')} RED)")
        return 0
    finally:
        store.close()


def cmd_news(args) -> int:
    """External-news triage for holdings + a MARKET macro query. The judgment is
    the SAME keyword vocabulary as announcements (data.announce) — news adds a
    feed, not a second opinion. Feed trouble is reported as feed trouble; a dead
    feed is never rendered as a quiet day."""
    from indtrader.data import news
    from indtrader.data.store import BarStore

    store = BarStore()
    try:
        held = _held_symbols()
        fetch_ok = True
        fetch_syms = [s.upper() for s in (args.symbols or []) if s.upper() != "MARKET"]
        if not args.no_fetch:
            # fetch symbols named on the command line; none named -> the book's holdings
            res = news.fetch_news(fetch_syms or held, store)
            print(f"[news] fetch: transport={res['transport']} stored_new={res['stored_new']} "
                  f"queries={res['queries']} skipped_rows={res['skipped_rows']}")
            for bad in res["unreachable"] + res["failed"]:
                print(f"[news] feed problem: {bad}")
            if not res["ok"]:
                print("[news] EVERY feed query failed — results below are UNKNOWN, not quiet",
                      file=sys.stderr)
                fetch_ok = False
        syms = [s.upper() for s in (args.symbols or (held + ["MARKET"]))]
        print(news.format_alert(news.triaged_recent(store, syms, days=args.days)))
        return 0 if fetch_ok else 1
    finally:
        store.close()


def cmd_events(args) -> int:
    """Results calendar: store/surface dates and the plan's size rule per symbol."""
    from indtrader.data.store import BarStore
    from indtrader.research import events

    store = BarStore()
    try:
        if not args.no_fetch:
            print(f"[events] fetch: {events.fetch_results_calendar(store)}")
        if args.symbol:
            qty, note = events.size_gate(store, args.symbol.upper(), args.qty)
            print(f"{args.symbol.upper()}: {note} -> allowed qty {qty} (of {args.qty})")
            return 0
        rows = store.con.execute(
            "SELECT symbol, d FROM events WHERE kind='results' AND d >= date('now') ORDER BY d"
        ).fetchall() if store.con.execute(
            "SELECT name FROM sqlite_master WHERE name='events'").fetchone() else []
        print("upcoming results:", ", ".join(f"{s} {d}" for s, d in rows[:40]) or
              "none stored (fetch on a normal network)")
        return 0
    finally:
        store.close()


def cmd_review(args) -> int:
    from indtrader.alerts.review import build_weekly_review
    from indtrader.alerts.telegram import send
    from indtrader.config import get_settings

    text = build_weekly_review()
    print(text)
    s = get_settings()
    if not args.no_telegram and s.telegram_bot_token.strip() and s.telegram_chat_id.strip():
        ok, why = send(text, s)
        print(f"[review] telegram {'sent' if ok else 'not sent'}: {why}")
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


def cmd_gate(args) -> int:
    """Walk-forward + plateau gate over one symbol. Exit 2 means the strategy FAILED
    the gate — that is a result, not an error, but CI/scripts can key on it."""
    from indtrader.backtest.costs import DeliveryCosts
    from indtrader.backtest.strategies import REGISTRY
    from indtrader.backtest.walkforward import walk_forward_gate

    df = _load_or_fetch(BarStore(), args.symbol)
    if df.empty:
        print("no data", file=sys.stderr)
        return 1
    grid: dict[str, list] = {}
    for item in args.param or []:
        k, _, vs = item.partition("=")
        if not vs:
            print(f"bad --param '{item}' (want name=v1,v2,...)", file=sys.stderr)
            return 1
        grid[k.strip()] = [float(x) if "." in x else int(x) for x in vs.split(",")]
    if not grid:
        print("needs at least one --param, e.g. --param period=150,200,250", file=sys.stderr)
        return 1
    rep = walk_forward_gate(df, REGISTRY[args.strategy], grid, costs=DeliveryCosts(),
                            metric=args.metric, warmup=args.warmup, test_days=args.test_days,
                            progress=lambda m: print(f"[gate] {m}"))
    print(pd.DataFrame(rep.as_rows()).to_string(index=False))
    print(f"{'PASS' if rep.passed else 'FAIL'}: {rep.strategy} metric={rep.metric}")
    for r in rep.reasons:
        print(f" - {r}")
    return 0 if rep.passed else 2


def cmd_gate_universe(args) -> int:
    """Batch walk-forward gate with split-gap exclusion — the laptop's 10-year
    gate run. Symbols with unexplained cliffs are EXCLUDED and counted, never
    folded. Exit: 1 nothing ran, 2 any executed symbol failed, 0 all passed."""
    from pathlib import Path

    from indtrader.backtest.costs import DeliveryCosts
    from indtrader.backtest.strategies import REGISTRY
    from indtrader.backtest.universegate import run_universe_gate
    from indtrader.data.universe import load_universe

    grid: dict[str, list] = {}
    for item in args.param or []:
        k, _, vs = item.partition("=")
        if not vs:
            print(f"bad --param '{item}' (want name=v1,v2,...)", file=sys.stderr)
            return 1
        grid[k.strip()] = [float(x) if "." in x else int(x) for x in vs.split(",")]
    if not grid:
        print("needs at least one --param, e.g. --param period=150,200,250", file=sys.stderr)
        return 1
    store = BarStore()
    syms = load_universe(Path(args.file)) if args.file else args.symbols
    if not syms:
        print("no symbols — pass some or --file universe.txt", file=sys.stderr)
        return 1
    if args.include_gappy:
        print("WARNING: --include-gappy runs folds over UNEXPLAINED split cliffs — "
              "numbers on those symbols are fiction until factors land", file=sys.stderr)
    rep = run_universe_gate(store, syms, REGISTRY[args.strategy], grid,
                            costs=DeliveryCosts(), metric=args.metric,
                            include_gappy=args.include_gappy,
                            warmup=args.warmup, test_days=args.test_days,
                            progress=lambda m: print(f"[gate-universe] {m}"))
    for r in rep.rows:
        mark = {"PASS": "OK  ", "FAIL": "FAIL", "EXCLUDED": "SKIP",
                "TOO_SHORT": "SHORT", "NODATA": "NO-DATA"}.get(r.status, r.status)
        print(f"{mark:<8} {r.symbol:<14} {r.detail[:130]}")
    print(rep.summary_line())
    counts = rep.counts()
    if not counts.get("PASS", 0) and not counts.get("FAIL", 0):
        print("nothing was gated — every symbol excluded/missing/short (that is an "
              "unknown, not a pass)", file=sys.stderr)
        return 1
    return 2 if counts.get("FAIL", 0) else 0


def cmd_drafts(args) -> int:
    """Evening DRAFT builder: top scan picks -> plan drafts with evidence in the
    reason. Without --apply it prints and journals the signal; with --apply the
    drafts enter the journal as OPEN plans a human then executes or cancels.
    This never trades — the only path into a position stays: human fills at the
    broker, `fill --plan` records it."""
    from indtrader.execution.planlog import PlanLog
    from indtrader.models import OrderPlan
    from indtrader.research.paperloop import draft_for

    store = BarStore()
    log = PlanLog()
    try:
        picks = log.con.execute(
            "SELECT payload FROM signals WHERE source='scan' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if not picks:
            print("no scan signal stored — run `indtrader scan` first (the draft chain "
                  "starts from tonight's shortlist, not from nowhere)", file=sys.stderr)
            return 1
        data = json.loads(picks[0])
        rows = data.get("picks") or []
        scale = float(data.get("exposure_scale") or 1.0)
        equity = args.equity * scale
        open_syms = {str(r[2]).upper() for r in log.open_plans()}
        held = log.executed_positions()
        drafts, skipped, dupes = [], [], []
        for row in rows[:args.top]:
            sym = str(row.get("symbol", "")).upper()
            if not sym:
                continue
            if sym in open_syms or sym in held:
                dupes.append(sym)
                continue
            d = draft_for(store, sym, equity, cash=args.cash, index=args.index)
            (skipped if d.skip else drafts).append(d)
        print(f"drafts (equity {equity:,.0f} = {args.equity:,.0f} x regime {scale}):")
        for d in drafts:
            print(f"  BUY {d.qty:>5} {d.symbol:<12} entry {d.entry:<10.2f} stop {d.stop:<10.2f} "
                  f"target {d.target:<10.2f} {d.reason[:80]}")
        for d in skipped:
            print(f"  skip {d.symbol:<12} {d.skip}")
        for s in dupes:
            print(f"  skip {s:<12} already open/held (no duplicate drafts)")
        if args.apply:
            n = 0
            for d in drafts:
                pid = log.add_plan(OrderPlan(symbol=d.symbol, action="BUY", qty=d.qty,
                                             entry=d.entry, stop=d.stop, target=d.target,
                                             reason=d.reason, setup="scan-draft"))
                n += 1
                print(f"  stored plan #{pid}: {d.symbol} (execute by hand, then: "
                      f"fill {d.symbol} BUY {d.qty} {d.entry} --plan {pid})")
            log.expire_stale(__import__("datetime").date.today())
            print(f"applied {n} draft plan(s) — they sit OPEN until a human fills or cancels")
        else:
            print("(printed only — nothing stored; add --apply to journal them as open plans)")
        return 0
    finally:
        log.close()


def cmd_brief(args) -> int:
    """Morning brief: open plans vs closed bars, TTL-expired first, event rule
    applied, stale rows named. Text for a human; zero actions."""
    from indtrader.execution.planlog import PlanLog
    from indtrader.research.paperloop import morning_brief

    store = BarStore()
    log = PlanLog()
    try:
        print(morning_brief(store, log, index=args.index))
        return 0
    finally:
        log.close()


def cmd_montecarlo(args) -> int:
    from indtrader.backtest.costs import DeliveryCosts
    from indtrader.backtest.engine import Backtester
    from indtrader.backtest.montecarlo import monte_carlo
    from indtrader.backtest.strategies import REGISTRY

    df = _load_or_fetch(BarStore(), args.symbol)
    if df.empty:
        print("no data", file=sys.stderr)
        return 1
    res = Backtester(costs=DeliveryCosts(), capital=args.capital).run(
        df, REGISTRY[args.strategy](), symbol=args.symbol.upper())
    out = monte_carlo(res, capital=args.capital, seed=args.seed)
    print(json.dumps(out, indent=1))
    return 0 if out.get("ok") else 1


def cmd_weights(args) -> int:
    from indtrader.portfolio.weights import weights

    store = BarStore()
    prices = {}
    for sym in args.symbols or load_universe(args.file):
        df = _load_or_fetch(store, sym)
        if df.empty:
            print(f"[weights] no data for {sym} — refusing to size on unknown", file=sys.stderr)
            return 1
        prices[sym.upper()] = df["close"]
    try:
        w, used = weights(prices, method=args.method, max_w=args.max_w)
    except (ValueError, ImportError) as e:
        print(f"[weights] {e}", file=sys.stderr)
        return 1
    for k, v in sorted(w.items(), key=lambda kv: -kv[1]):
        print(f"{k:14s} {v:7.4f}")
    print(f"{'CASH(residual)':14s} {max(round(1 - sum(w.values()), 4), 0.0):7.4f}  method={used}")
    return 0


def cmd_xirr(args) -> int:
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.actions import apply_actions, extra_flows, load_actions
    from indtrader.portfolio.valuation import book_xirr, held_quantities, resolve_marks

    store, log = BarStore(), PlanLog()
    try:
        fills = apply_actions(log.fills(), load_actions(log))
        last = resolve_marks(store, held_quantities(fills))
        out = book_xirr(fills, last, extra_flows=extra_flows(load_actions(log)))
        print(json.dumps(out, indent=1))
        return 0 if out["ok"] else 1
    finally:
        store.close()
        log.close()


def cmd_riskreport(args) -> int:
    from indtrader.alerts.riskreport import build_weekly_risk_report
    from indtrader.alerts.telegram import send
    from indtrader.config import get_settings

    text = build_weekly_risk_report()
    print(text)
    s = get_settings()
    if not args.no_telegram and s.telegram_bot_token.strip() and s.telegram_chat_id.strip():
        ok, why = send(text, s)
        print(f"[riskreport] telegram {'sent' if ok else 'not sent'}: {why}")
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
    s = sub.add_parser("fetch-universe")
    s.add_argument("--file", default=None)
    s.add_argument("--skip-fresh", dest="skip_fresh", action="store_true",
                   help="skip symbols whose tail is current AND history reaches --start "
                        "(resume-friendly backfill; nightly loop does this by default)")
    s.set_defaults(fn=cmd_fetch_universe)
    s = sub.add_parser("backtest")
    s.add_argument("symbol")
    s.add_argument("--strategy", default="trend_sma200")
    s.add_argument("--costs", action="store_true", help="apply Indian delivery costs")
    s.add_argument("--adjusted", action="store_true",
                   help="use stored corporate-action factors (warns + falls back to RAW if none stored)")
    s.set_defaults(fn=cmd_backtest)
    s = sub.add_parser("rrg")
    s.add_argument("symbols", nargs="+"); s.add_argument("--bench", default="NIFTY"); s.set_defaults(fn=cmd_rrg)
    s = sub.add_parser("regime"); s.add_argument("--index", default="NIFTY"); s.set_defaults(fn=cmd_regime)
    s = sub.add_parser("scan")
    s.add_argument("--file", default=None, help="universe file (default: built-in sample)")
    s.add_argument("--index", default="NIFTY")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--no-journal", action="store_true", help="don't write the shortlist to the signal journal")
    s.add_argument("--max-stale", dest="max_stale", type=int, default=5,
                   help="dead-man: refuse scan if index's last bar is older than N business days (default 5)")
    s.add_argument("--ignore-stale", action="store_true", help="override the dead-man (not recommended)")
    s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("harvest", help="pre-March tax-loss harvesting candidates from open FIFO lots")
    s.add_argument("--asof", default=None, help="YYYY-MM-DD (default today)")
    s.set_defaults(fn=cmd_harvest)
    s = sub.add_parser("decay", help="paper book vs the strategy's own Monte-Carlo expectation band")
    s.add_argument("symbol")
    s.add_argument("--strategy", default="trend_sma200")
    s.add_argument("--capital", type=float, default=100_000.0)
    s.add_argument("--window", type=int, default=None, help="rolling paper window size (default 20)")
    s.set_defaults(fn=cmd_decay)
    s = sub.add_parser("import-csv", help="seed the fills ledger from a broker tradebook CSV")
    s.add_argument("file")
    s.add_argument("--format", default="generic", help="generic | zerodha-tradebook | angel (or use --map)")
    s.add_argument("--map", default=None, help="field=column pairs, e.g. dt=Trade Date,symbol=Symbol")
    s.add_argument("--dry-run", dest="dry_run", action="store_true")
    s.add_argument("--apply", action="store_true", help="actually write (default refuses)")
    s.set_defaults(fn=cmd_import_csv)
    s = sub.add_parser("shareholding", help="QoQ ownership deltas for holdings (record/fetch/list/alert)")
    s.add_argument("symbol", nargs="?", default=None)
    s.add_argument("--fetch", default=None, help="probe live shape for this symbol, store raw")
    s.add_argument("--set", default=None, help="record a quarter: SYM@PERIOD:promoter:fii:dii:public:pledge")
    s.add_argument("--holdings", default="", help="comma list for the alert view")
    s.set_defaults(fn=cmd_shareholding)
    s = sub.add_parser("coractions",
                       help="probe corporate actions (split factors) - raw stored, parse awaits shape")
    s.add_argument("symbol", nargs="?", default=None)
    s.add_argument("--from-raw", dest="from_raw", action="store_true",
                   help="offline: replay the confirmed-shape parser over stored payloads (no network)")
    s.add_argument("--set", dest="set_event", default=None, metavar="DATE|KIND|RATIO",
                   help="human-confirmed event, ratio = price multiplier (1:2 split -> 0.5)")
    s.add_argument("--gap-list", dest="gap_list", action="store_true",
                   help="batch: probe every symbol health would name as SPLIT-GAP")
    s.add_argument("--resolve", action="store_true",
                   help="offline: re-parse stored subjects with the CURRENT parser; "
                        "still-unknown rows print the exact --set command (never guesses)")
    s.add_argument("--file", default=None, help="universe file for --gap-list")
    s.add_argument("--limit", type=int, default=0, help="cap symbols for --gap-list")
    s.set_defaults(fn=cmd_coractions)
    s = sub.add_parser("backup", help="online-backup bars+plans, integrity-check, prune to --keep")
    s.add_argument("--dest", default=None)
    s.add_argument("--keep", type=int, default=14)
    s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("restore", help="restore one store from a verified backup")
    s.add_argument("kind", choices=["bars", "plans"])
    s.add_argument("backup")
    s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("dashboard", help="read-only one-page web dashboard on 127.0.0.1")
    s.add_argument("--host", default="127.0.0.1",
                   help="only relax beyond 127.0.0.1 inside a container you port-map yourself")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--universe", default=None,
                   help="universe file (default: data/universe.txt when it exists)")
    s.set_defaults(fn=cmd_dashboard)
    s = sub.add_parser("fetch-feeds"); s.add_argument("--days", type=int, default=7); s.set_defaults(fn=cmd_fetch_feeds)
    s = sub.add_parser("fetch-adj", help="store corporate-action factors (yfinance Adj Close / Close)")
    s.add_argument("symbols", nargs="*")
    s.add_argument("--file", default=None)
    s.add_argument("--start", default="2015-01-01")
    s.set_defaults(fn=cmd_fetch_adj)
    s = sub.add_parser("health", help="data-health gate: rows/gaps/staleness/adj-coverage vs index calendar")
    s.add_argument("--file", default=None)
    s.add_argument("--index", default="NIFTY")
    s.add_argument("--max-missing", dest="max_missing", type=int, default=2)
    s.add_argument("--max-stale", dest="max_stale", type=int, default=3)
    s.add_argument("--max-gaps", dest="max_gaps", type=int, default=20,
                   help="how many split-gap symbols to list (they are printed, never silently dropped)")
    s.set_defaults(fn=cmd_health)
    s = sub.add_parser("contract-test",
                       help="store closes vs NSE bhavcopy for one session (Phase-1 acceptance)")
    s.add_argument("--file", default=None)
    s.add_argument("--date", default=None, help="session YYYY-MM-DD (default: store's newest)")
    s.add_argument("--tolerance", type=float, default=0.01)
    s.add_argument("--min-checked", dest="min_checked", type=int, default=5)
    s.set_defaults(fn=cmd_contract_test)
    s = sub.add_parser("holidays", help="NSE CBM trading-holiday calendar + cross-check vs NIFTY bars")
    s.add_argument("--year", type=int, default=None)
    s.add_argument("--index", default="NIFTY")
    s.add_argument("--refresh-calendar", dest="refresh_calendar", action="store_true",
                   help="fetch the NSE holiday master into the store (offline drift ledger)")
    s.set_defaults(fn=cmd_holidays)
    s = sub.add_parser("notify"); s.set_defaults(fn=cmd_notify)
    s = sub.add_parser("bot", help="Telegram command interface (long-poll; reads + journal only)")
    s.set_defaults(fn=cmd_bot)
    s = sub.add_parser("pause", help="quiet the daily outbound loop (survives restarts)")
    s.set_defaults(fn=cmd_pause)
    s = sub.add_parser("resume", help="undo pause")
    s.set_defaults(fn=cmd_resume)
    s = sub.add_parser("mf-navs", help="fetch + store latest MF NAVs from AMFI (contract-checked)")
    s.set_defaults(fn=cmd_mf_navs)
    s = sub.add_parser("mf", help="MF slice of the book at stored NAVs; or scheme search with a text arg")
    s.add_argument("query", nargs="*")
    s.set_defaults(fn=cmd_mf)
    s = sub.add_parser("fill-mf", help="record one SIP instalment/redemption (fractional units, NAV-priced)")
    s.add_argument("symbol", help="AMFI scheme code (or MF:-prefixed)")
    s.add_argument("side", help="BUY | SELL")
    s.add_argument("units", type=float)
    s.add_argument("nav", type=float)
    s.add_argument("--date", default=None, help="YYYY-MM-DD (default today)")
    s.add_argument("--costs", type=float, default=0.0)
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_fill_mf)
    s = sub.add_parser("sips", help="SIP aggregation + money-weighted return of the MF slice")
    s.set_defaults(fn=cmd_sips)
    s = sub.add_parser("action", help="record a corporate action as the statement showed it")
    s.add_argument("symbol")
    s.add_argument("kind", help="dividend | bonus | split | redemption")
    s.add_argument("--qty", dest="qty", type=float, default=0.0,
                   help="units added (bonus/split) or redeemed (redemption)")
    s.add_argument("--cash", type=float, default=0.0, help="net cash received")
    s.add_argument("--tds", type=float, default=0.0, help="TDS withheld on that cash")
    s.add_argument("--date", default=None, help="YYYY-MM-DD payout/ex date (default today)")
    s.add_argument("--note", default="")
    s.set_defaults(fn=cmd_action)
    s = sub.add_parser("actions", help="list recorded corporate actions")
    s.set_defaults(fn=cmd_actions)
    s = sub.add_parser("rebalance", help="monthly rebalance proposals + tax lens (targets JSON: {SYM: weight})")
    s.add_argument("--file", required=True)
    s.add_argument("--tol", type=float, default=0.02, help="no-trade band as fraction of total")
    s.set_defaults(fn=cmd_rebalance)
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
    s.add_argument("--plan", type=int, default=None,
                   help="id of the OPEN written plan this fill executes (marks it executed)")
    s.set_defaults(fn=cmd_fill)
    s = sub.add_parser("plan", help="write a BUY plan (mandatory reason; stop < entry < target)")
    s.add_argument("symbol")
    s.add_argument("action", choices=["BUY"])
    s.add_argument("--qty", type=int, required=True)
    s.add_argument("--entry", type=float, required=True)
    s.add_argument("--stop", type=float, required=True)
    s.add_argument("--target", type=float, required=True)
    s.add_argument("--reason", default="", help="written rationale, >= 10 chars — mandatory")
    s.add_argument("--setup", default="", help="breakout | pullback | rotation-en | manual")
    s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("plans", help="list plans (open first; --all for everything recent)")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_plans)
    s = sub.add_parser("plan-mark", help="set a plan's status by hand (executed|cancelled|stale)")
    s.add_argument("id", type=int)
    s.add_argument("status")
    s.set_defaults(fn=cmd_plan_mark)
    s = sub.add_parser("attribution", help="realized P&L by setup x index-trend-at-entry")
    s.add_argument("--index", default="NIFTY")
    s.set_defaults(fn=cmd_attribution)
    s = sub.add_parser("compare", help="A/B every strategy on one symbol, same window and costs")
    s.add_argument("symbol")
    s.add_argument("--strategies", default=None, help="comma list (default: all in REGISTRY)")
    s.add_argument("--costs", action="store_true",
                   help="apply full delivery costs + slippage (default: gross, none)")
    s.add_argument("--adjusted", action="store_true", help="use adjusted bars")
    s.set_defaults(fn=cmd_compare)
    s = sub.add_parser("tax", help="FIFO realized P&L + CGT estimate per financial year")
    s.add_argument("--setoff", action="store_true",
                   help="apply loss set-off order + 8-year carry-forward instead of the upper bound")
    s.set_defaults(fn=cmd_tax)
    s = sub.add_parser("ann", help="announcement triage for holdings only (RED/AMBER keywords)")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--no-fetch", action="store_true", help="triage stored rows only")
    s.add_argument("--telegram", action="store_true", help="also send the alert when configured")
    s.set_defaults(fn=cmd_ann)
    s = sub.add_parser("news",
                       help="external news (Google News RSS) triaged with the SAME ann keywords; holdings + MARKET")
    s.add_argument("symbols", nargs="*", help="override symbols; default = holdings + MARKET")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--no-fetch", dest="no_fetch", action="store_true",
                   help="offline: triage stored rows only, no network")
    s.set_defaults(fn=cmd_news)
    s = sub.add_parser("events", help="results calendar + the no-entry/half-size plan rule")
    s.add_argument("symbol", nargs="?")
    s.add_argument("--qty", type=int, default=0, help="proposed qty to run through the size gate")
    s.add_argument("--no-fetch", action="store_true")
    s.set_defaults(fn=cmd_events)
    s = sub.add_parser("review", help="Sunday weekly review (market, book vs plans, journal, alerts, health)")
    s.add_argument("--no-telegram", action="store_true")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("pbacktest", help="portfolio-level backtest: shared cash, concurrent positions")
    s.add_argument("symbols", nargs="*")
    s.add_argument("--file", default=None)
    s.add_argument("--index", default="NIFTY")
    s.add_argument("--strategy", default="trend_sma200")
    s.add_argument("--capital", type=float, default=1_000_000.0)
    s.add_argument("--positions", type=int, default=5)
    s.set_defaults(fn=cmd_pbacktest)
    s = sub.add_parser("gate", help="walk-forward + parameter-plateau gate; exit 2 = strategy failed")
    s.add_argument("symbol")
    s.add_argument("--strategy", default="trend_sma200")
    s.add_argument("--param", action="append", help="name=v1,v2 (repeatable), e.g. period=150,200,250")
    s.add_argument("--metric", default="sharpe", choices=["sharpe", "cagr", "profit_factor"])
    s.add_argument("--warmup", type=int, default=400)
    s.add_argument("--test-days", dest="test_days", type=int, default=252)
    s.set_defaults(fn=cmd_gate)
    s = sub.add_parser("gate-universe", help="batch gate with split-gap exclusion: "
                       "gappy symbols are skipped and counted, never silently folded")
    s.add_argument("symbols", nargs="*")
    s.add_argument("--file", default=None, help="universe file (symbol per line)")
    s.add_argument("--strategy", default="trend_sma200")
    s.add_argument("--param", action="append", help="name=v1,v2 (repeatable), e.g. period=150,200,250")
    s.add_argument("--metric", default="sharpe", choices=["sharpe", "cagr", "profit_factor"])
    s.add_argument("--warmup", type=int, default=400)
    s.add_argument("--test-days", dest="test_days", type=int, default=252)
    s.add_argument("--include-gappy", dest="include_gappy", action="store_true",
                   help="run the gate on split-gap symbols anyway (loudly discouraged)")
    s.set_defaults(fn=cmd_gate_universe)
    s = sub.add_parser("drafts", help="evening: last scan shortlist -> DRAFT plans "
                       "(sizing + results rule applied; --apply journals them as open plans)")
    s.add_argument("--top", type=int, default=5, help="how many scan picks to draft (default 5)")
    s.add_argument("--equity", type=float, default=1_000_000.0,
                   help="paper equity; scaled by the stored regime exposure_scale")
    s.add_argument("--cash", type=float, default=None, help="cap drafts by cash on hand")
    s.add_argument("--index", default="NIFTY")
    s.add_argument("--apply", action="store_true",
                   help="store drafts as OPEN plans (still human-executed — nothing here trades)")
    s.set_defaults(fn=cmd_drafts)
    s = sub.add_parser("brief", help="morning: open plans vs closed bars — TTL, gaps, "
                       "stops, results rule; text only, zero actions")
    s.add_argument("--index", default="NIFTY")
    s.set_defaults(fn=cmd_brief)
    s = sub.add_parser("montecarlo", help="bootstrap distribution of outcomes from costed trades")
    s.add_argument("symbol")
    s.add_argument("--strategy", default="trend_sma200")
    s.add_argument("--capital", type=float, default=1_000_000.0)
    s.add_argument("--seed", type=int, default=12345)
    s.set_defaults(fn=cmd_montecarlo)
    s = sub.add_parser("weights", help="risk-balanced portfolio weights (inverse_vol | hrp)")
    s.add_argument("symbols", nargs="*")
    s.add_argument("--file", default=None)
    s.add_argument("--method", default="inverse_vol", choices=["inverse_vol", "hrp"])
    s.add_argument("--max-w", dest="max_w", type=float, default=0.25)
    s.set_defaults(fn=cmd_weights)
    s = sub.add_parser("xirr", help="money-weighted return of the actual fills ledger")
    s.set_defaults(fn=cmd_xirr)
    s = sub.add_parser("riskreport", help="weekly risk report: regime, book, journal, data health")
    s.add_argument("--no-telegram", action="store_true")
    s.set_defaults(fn=cmd_riskreport)
    s = sub.add_parser("universe")
    s.add_argument("--index", default="nifty 50")
    s.add_argument("--out", default="data/universe.txt")
    s.set_defaults(fn=cmd_universe)
    s = sub.add_parser("ai"); s.add_argument("symbol"); s.set_defaults(fn=cmd_ai)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
