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


def run_once(universe_file: str | None = None) -> dict[str, int]:
    res = run_etl(universe_file=Path(universe_file) if universe_file else None)
    empty = [s for s, n in res.items() if n == 0]
    print(f"[etl-loop] {datetime.now(IST):%Y-%m-%d %H:%M} IST — stored {len(res) - len(empty)}/{len(res)}"
          + (f"; EMPTY: {', '.join(empty)}" if empty else ""))
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
