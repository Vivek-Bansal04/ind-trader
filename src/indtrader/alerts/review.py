"""Sunday review generator (plan section D): a 15-minute review instead of a 3-hour
assembly. One markdown text over local stores: benchmark week, book vs plan, journal
hygiene, announcements on holdings, data health. No new truths — only arrangements.
"""
from __future__ import annotations

from datetime import date, timedelta


def build_weekly_review(settings=None, scan_rows: int = 10) -> str:
    from indtrader.config import get_settings
    from indtrader.data.announce import triaged_recent, format_alert
    from indtrader.data.store import BarStore
    from indtrader.data.universe import load_universe
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.valuation import book_xirr, held_quantities
    from indtrader.research.calendar import index_sessions, missing_sessions, staleness_days
    from indtrader.research.regime import flip_note, regime_from_data

    s = settings or get_settings()
    store = BarStore()
    log = PlanLog()
    try:
        L = [f"# ind-trader weekly review — {date.today()}", ""]
        # 1. benchmark week
        idx = store.load("NIFTY")
        if not idx.empty:
            wk = idx["close"].tail(6)
            chg = float(wk.iloc[-1] / wk.iloc[0] - 1) if len(wk) >= 2 else 0.0
            L.append(f"## Market\nNIFTY 5-session: {chg:+.2%} at {float(wk.iloc[-1]):,.0f}")
            closes = {}
            for sym in load_universe(s.data_dir / "universe.txt"):
                df = store.load(sym)
                if not df.empty and sym != "NIFTY":
                    closes[sym] = df["close"]
            if closes:
                r = regime_from_data(closes, idx["close"], idx["volume"])
                L.append(f"Regime {r.label} (exposure {r.exposure_scale:g}) — "
                         f"breadth {'unknown' if r.breadth is None else format(r.breadth, '.0%')}")
                note = flip_note(r, r.ftd)
                if note:
                    L.append(note)
        else:
            L.append("## Market\nNIFTY bars missing in store — ETL gap, fix before trusting anything below")

        # 2. book vs plan
        fills = log.fills()
        held = held_quantities(fills)
        marks = {}
        for sym in held:
            df = store.load(sym)
            marks[sym] = float(df["close"].iloc[-1]) if not df.empty else None
        rep = book_xirr(fills, marks)
        L.append("## Book")
        if not fills:
            L.append("No fills yet — paper loop not started.")
        else:
            L.append(f"XIRR: {'uncomputable/missing marks' if not rep['ok'] else ('unbounded' if rep['xirr'] is None else format(rep['xirr'], '.1%'))}"
                     f" | open value {rep.get('open_value', 0):,.0f} | {len(held)} positions")
            for sym, q in sorted(held.items()):
                px = marks.get(sym)
                plan = log.con.execute(
                    "SELECT entry, stop, target, reason FROM plans WHERE symbol=? "
                    "AND status='executed' ORDER BY created DESC LIMIT 1", [sym]).fetchone()
                if plan:
                    px_s = f"{px:,.1f}" if px else "no mark"
                    L.append(f"- {sym} qty {q} @ {px_s} | plan entry {plan[0]}, stop {plan[1]}, "
                             f"target {plan[2]} | thesis: {str(plan[3])[:80]}")
                else:
                    L.append(f"- {sym} qty {q} — NO executed plan behind this fill (journal gap)")

        # 3. journal hygiene
        open_plans = log.open_plans()
        n_stale = log.con.execute("SELECT COUNT(*) FROM plans WHERE status='stale'").fetchone()[0]
        L.append("\n## Journal\n"
                 f"{len(fills)} fills | {len(open_plans)} open plans | {n_stale} expired (stale) "
                 f"| every open plan older than 2 days should be re-confirmed or cancelled")

        # 4. announcements on holdings only
        L.append("\n## Announcements (holdings)")
        L.append(format_alert(triaged_recent(store, list(held), days=7)))

        # 5. data health (worst offenders)
        if not idx.empty:
            sessions = index_sessions(idx)
            bad = []
            for sym in sorted(set(load_universe(s.data_dir / "universe.txt"))):
                df = store.load(sym, validate=False)
                if df.empty:
                    bad.append(f"{sym}: no data")
                    continue
                miss = len(missing_sessions(df.index, sessions))
                stale = staleness_days(df.index, sessions) or 0
                if miss > 2 or stale > 3:
                    bad.append(f"{sym}: {miss} missing sessions, {stale} stale")
            L.append("\n## Data health\n" + ("all universe symbols complete" if not bad else "\n".join(bad[:10])))
        return "\n".join(L)
    finally:
        store.close()
        log.close()
