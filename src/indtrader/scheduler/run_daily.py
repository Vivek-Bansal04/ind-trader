"""Daily 16:05 IST ETL loop (weekday-guarded). Used by `docker compose up etl-loop`
or directly: python -m indtrader.scheduler.run_daily.

Kept deliberately dumb: sleep 60s, check clock, fetch universe, log results.
A missed run self-heals next evening (upserts are idempotent)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from indtrader.scheduler.etl import run_etl

IST = timezone(timedelta(hours=5, minutes=30))


def run_once(universe_file: str | None = None) -> dict[str, int | None]:
    res = run_etl(universe_file=Path(universe_file) if universe_file else None)
    # None = fresh-skipped, 0 = fetched but no data, >0 = rows stored. Collapsing
    # the two was the round-6 report bug: a healthy all-fresh evening printed
    # "EMPTY: <all 499 symbols>", which is exactly the false alarm that trains
    # an operator to ignore the loop.
    stored = sum(1 for n in res.values() if n)
    skipped = sum(1 for n in res.values() if n is None)
    empty = [s for s, n in res.items() if n == 0]
    print(f"[etl-loop] {datetime.now(IST):%Y-%m-%d %H:%M} IST — stored {stored}/{len(res)}"
          f", fresh-skipped {skipped}"
          + (f"; EMPTY: {', '.join(empty[:12])}{' ...' if len(empty) > 12 else ''}"
             if empty else ""))
    # best-effort evidence feeds after candles (silent no-op without NseIndiaApi)
    try:
        from indtrader.data.feeds import fetch_feeds

        feeds_res = fetch_feeds()
        if feeds_res["status"] != "ok":
            print(f"[etl-loop] feeds: {feeds_res['status']} (kept last-known)")
    except Exception as e:
        print(f"[etl-loop] feeds failed (non-fatal): {e}")
    # external news for held names (Google RSS; a dead feed prints, never fakes quiet)
    try:
        from indtrader.data import news
        from indtrader.data.store import BarStore
        from indtrader.execution.planlog import PlanLog
        from indtrader.portfolio.valuation import held_quantities

        log = PlanLog()
        try:
            held = sorted(held_quantities(log.fills()))
        finally:
            log.close()
        store = BarStore()
        try:
            res = news.fetch_news(held, store)
        finally:
            store.close()
        print(f"[etl-loop] news: {res['stored_new']} new rows / {res['queries']} queries"
              + ("" if res["ok"] else " — ALL FEEDS DOWN, news view is UNKNOWN not quiet"))
    except Exception as e:
        print(f"[etl-loop] news failed (non-fatal): {e}")
    # results calendar for the holdings countdown (runtime method probe — an
    # unavailable source is REPORTED, and the countdown then names its unknowns)
    try:
        from indtrader.data.store import BarStore
        from indtrader.research import events

        store = BarStore()
        try:
            print(f"[etl-loop] events: {events.fetch_results_calendar(store)}")
        finally:
            store.close()
    except Exception as e:
        print(f"[etl-loop] events fetch failed (non-fatal): {e}")
    # holdings watchers: results countdown + good news on held names, at most
    # once per event (dedup rides the signals table). The report prints either
    # way — unconfigured or paused is STATED, never rendered as a quiet evening.
    try:
        from indtrader.alerts.holdings import send_holdings_alerts
        from indtrader.data.store import BarStore
        from indtrader.execution.planlog import PlanLog

        store, log = BarStore(), PlanLog()
        try:
            rep = send_holdings_alerts(store, log)
            if rep["sent"]:
                print(f"[etl-loop] holdings alerts sent: results={len(rep['results_sent'])} "
                      f"news={len(rep['news_sent'])}")
            else:
                print(f"[etl-loop] holdings alerts: {rep['reason'] or 'nothing sent'}")
        finally:
            store.close()
            log.close()
    except Exception as e:
        print(f"[etl-loop] holdings alerts failed (non-fatal): {e}")
    # evening briefing to Telegram when configured (print-only otherwise)
    try:
        from indtrader.alerts.briefing import maybe_notify

        sent, reason = maybe_notify()
        if not sent:
            print(f"[etl-loop] notify: {reason}")
    except Exception as e:
        print(f"[etl-loop] notify failed (non-fatal): {e}")
    return res


def main(run_now: bool = False) -> None:
    first = run_now
    while True:
        now = datetime.now(IST)
        if first or (now.weekday() < 5 and (now.hour, now.minute) >= (16, 5) and now.hour < 23):
            # run once per day: after firing, wait out the day
            try:
                run_once()
                fired = True
            except Exception as e:  # never die; the loop is the watchdog
                print(f"[etl-loop] run failed: {e}")
                fired = False
            if fired:
                while datetime.now(IST).date() == now.date():
                    time.sleep(600)
        first = False
        time.sleep(60)


if __name__ == "__main__":
    main(run_now=True)
