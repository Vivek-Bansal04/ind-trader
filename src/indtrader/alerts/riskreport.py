"""Weekly risk report (Phase 3 deliverable): one honest text — regime light, actual
book with money-weighted return, plan-vs-executed drift, universe data health.
Everything derived from local stores; unknown is printed as unknown."""
from __future__ import annotations

from datetime import date


def build_weekly_risk_report(settings=None) -> str:
    from indtrader.config import get_settings
    from indtrader.data.store import BarStore
    from indtrader.data.universe import load_universe
    from indtrader.execution.planlog import PlanLog
    from indtrader.portfolio.valuation import book_xirr, held_quantities

    s = settings or get_settings()
    lines = [f"ind-trader WEEKLY RISK REPORT {date.today()}", f"mode={s.execution_mode}"]
    store = BarStore()
    log = PlanLog()
    try:
        # --- regime block ---
        from indtrader.research.regime import flip_note, regime_from_data
        idx = store.load("NIFTY")
        closes, ivol = {}, None
        for sym in load_universe(s.data_dir / "universe.txt"):
            df = store.load(sym)
            if not df.empty and sym != "NIFTY":
                closes[sym] = df["close"]
        if closes and not idx.empty:
            r = regime_from_data(closes, idx["close"], idx["volume"])
            lines.append(f"Regime {r.label} | exposure {r.exposure_scale:g} | "
                         f"breadth {'unknown' if r.breadth is None else format(r.breadth, '.2f')}")
            note = flip_note(r, r.ftd)
            if note:
                lines.append(note)
        else:
            lines.append("Regime unknown — no NIFTY/universe bars in store")

        # --- actual book: marks, XIRR, concentration ---
        fills = log.fills()
        held = held_quantities(fills)
        last_close = {}
        for sym in held:
            df = store.load(sym)
            last_close[sym] = float(df["close"].iloc[-1]) if not df.empty else None
        rep = book_xirr(fills, last_close)
        if not fills:
            lines.append("Book: no fills recorded yet (paper loop not started)")
        elif not rep["ok"]:
            lines.append(f"Book: XIRR unavailable — {rep['reason']}"
                         + (f" [{','.join(rep.get('missing_marks', []))}]" if rep.get("missing_marks") else ""))
        else:
            top = sorted(((q * float(last_close[k]), k) for k, q in held.items()), reverse=True)[:3]
            top_s = ", ".join(f"{k} {v / rep['open_value']:.0%}" for v, k in top if rep["open_value"] > 0)
            lines.append(f"Book: XIRR {'unbounded' if rep['xirr'] is None else format(rep['xirr'], '.1%')}"
                         f" | invested {rep['invested']:,.0f} | open value {rep['open_value']:,.0f}"
                         f" | {len(held)} positions | top: {top_s or 'n/a'}")

        # --- plan vs executed (journal honesty) ---
        open_plans = log.open_plans()
        lines.append(f"Journal: {len(fills)} fills, {len(open_plans)} open plans "
                     f"(every fill must trace to a plan with a rationale)")

        # --- universe data health (compact) ---
        from indtrader.research.calendar import index_sessions, missing_sessions, staleness_days
        if not idx.empty:
            sessions = index_sessions(idx)
            bad = []
            for sym in sorted(set(load_universe(s.data_dir / "universe.txt"))):
                df = store.load(sym, validate=False)
                if df.empty:
                    bad.append(sym + " (no data)")
                    continue
                miss = len(missing_sessions(df.index, sessions))
                stale = staleness_days(df.index, sessions) or 0
                if miss > 2 or stale > 3:
                    bad.append(f"{sym} (gap {miss}, stale {stale})")
            lines.append("Data health: OK" if not bad else "Data health PROBLEMS: " + ", ".join(bad[:10]))
    finally:
        store.close()
        log.close()
    return "\n".join(lines)
