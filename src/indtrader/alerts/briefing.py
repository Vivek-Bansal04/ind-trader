"""Evening briefing text: regime light + shortlist, assembled from local store only.

Works with whatever data exists (no provider access) — on a machine that hasn't
fetched, it honestly says so instead of showing stale numbers as fresh.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from indtrader.alerts.telegram import inr, send
from indtrader.config import Settings, get_settings
from indtrader.data.universe import load_universe
from indtrader.research.regime import flip_note

TOP = 5


def format_briefing(day: str, regime, table: pd.DataFrame | None, missing: list[str] | None = None) -> str:
    lines = [f"ind-trader {day}"]
    if regime is not None:
        b = "n/a" if regime.breadth is None else f"{regime.breadth:.2f}"
        lines.append(f"Regime {regime.label} | exposure {regime.exposure_scale:g} | breadth {b}")
    if table is None or table.empty:
        lines.append("No shortlist — no candle data in store yet (run fetch-universe).")
    else:
        picks = table[table["tradable"] == True].head(TOP)  # noqa: E712
        lines.append("Shortlist:" if not picks.empty else "Shortlist: nothing above the trend gate")
        for r in picks.itertuples():
            bits = [str(r.symbol), str(r.close), f"score {r.score:g}", str(r.quadrant)]
            mom = getattr(r, "mom_6m", float("nan"))
            if pd.notna(mom):
                bits.append(f"mom6m {mom:+.0%}")
            dp = getattr(r, "deliv_per", float("nan"))
            if pd.notna(dp):
                bits.append(f"deliv {dp:g}%")
            nb = getattr(r, "net_bulk", float("nan"))
            if pd.notna(nb) and nb != 0:
                bits.append(f"netBulk {inr(nb)}")
            note = str(getattr(r, "note", "") or "")
            lines.append(" - " + "  ".join(bits) + (f"  [{note}]" if note else ""))
    if missing:
        lines.append(f"no data: {', '.join(missing[:8])}")
    note = flip_note(regime, getattr(regime, "ftd", None))
    if note:
        lines.append(note)
    return "\n".join(lines)


def build_briefing(settings: Settings | None = None) -> str:
    from indtrader.data import feeds
    from indtrader.data.store import BarStore
    from indtrader.research import events
    from indtrader.research.regime import classify, fraction_above_200dma, regime_from_data
    from indtrader.research.scanner import scan

    s = settings or get_settings()
    store = BarStore()
    try:
        closes, volumes, bars, extras, missing = {}, {}, {}, {}, []
        for sym in load_universe(s.data_dir / "universe.txt"):
            df = store.load(sym)
            if df.empty:
                missing.append(sym)
                continue
            closes[sym] = df["close"]
            volumes[sym] = df["volume"]
            bars[sym] = df
            extra = {"deliv_per": feeds.latest_delivery_per(store, sym),
                     "deliv_avg": feeds.avg_delivery_per(store, sym),
                     "net_bulk": feeds.net_bulk_value(store, sym),
                     "results_in": events.sessions_until(store, sym)}
            if any(v is not None for v in extra.values()):
                extras[sym] = extra
        idx = store.load("NIFTY")
        regime = None
        if closes:
            if not idx.empty:
                regime = regime_from_data(closes, idx["close"], idx["volume"])
            else:
                regime = classify(fraction_above_200dma(closes), None)
        table = scan(closes, bench=(idx["close"] if not idx.empty else None),
                     regime=regime, top=TOP, extras=extras,
                     volumes=volumes, bars=bars) if closes else None
        text = format_briefing(str(date.today()), regime, table, missing=missing)
        # News line: STORED triage only (fetch is the loop's job); empty when
        # nothing keyword-flagged — same vocabulary as the ann section, 3d window.
        try:
            from indtrader.data import news
            from indtrader.execution.planlog import PlanLog
            from indtrader.portfolio.valuation import held_quantities

            log = PlanLog()
            try:
                held = sorted(held_quantities(log.fills()))
            finally:
                log.close()
            flagged = news.triaged_recent(store, held + ["MARKET"], days=3)
            if flagged:
                text += "\nNews flagged: " + "; ".join(
                    f"[{m.severity}] {m.symbol} {m.title[:70]}" for m in flagged[:5])
        except Exception as e:
            text += f"\nNews section unavailable: {type(e).__name__}"
        return text
    finally:
        store.close()


def maybe_notify(settings: Settings | None = None) -> tuple[bool, str]:
    """Print the briefing; send to Telegram if configured and not paused. Never raises."""
    s = settings or get_settings()
    text = build_briefing(s)
    print(text)
    if not is_on(s):
        return False, "telegram not configured"
    from indtrader.alerts.pause import is_paused

    if is_paused(s):
        return False, "loop PAUSED (resume: indtrader resume, or /resume on the bot)"
    return send(text, s)


def is_on(s: Settings) -> bool:
    return bool(s.telegram_bot_token.strip() and s.telegram_chat_id.strip())
