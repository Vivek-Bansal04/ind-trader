# ROUND 6 — laptop checklist (~20 min). Round-5 was gold: Phase 1 closed at
# scale, and three real problems surfaced (run_once timeout, news 403, split
# factors missing). This round validates the fixes for all three.

Code changes pulled this round (pushed as `0ad6156` — your pull must include
it or STOP):

- `etl`: new-listing freshness — symbols listed after `--start` with >= 60
  bars and a current tail now count as fresh (that was the 120s+ timeout).
- `data/http.py`: shared transport for news + calendar — curl_cffi Chrome
  impersonation where installed (the only route proven through this network's
  WAF), urllib fallback, and the fetch line NAMES the transport that ran.
- `holidays --refresh-calendar`: stores the NSE holiday master in the store;
  `holidays` then prints a permanent drift ledger (exchange-holiday dates
  that have NIFTY bars — your 4 disputed dates should appear).
- `coractions` probe v2: tries every corporate-actions method/arg-form and
  prints each call's payload SHAPE (round 5's `actions()` answer was an
  EMPTY list — too thin to build on). New `--from-raw` replays the parser
  over stored payloads, no network.

Start with:

```bash
cd ~/Desktop/ind-trader
git pull
git log --oneline -1   # must be 0ad6156 or newer
```

---

## 1. News, un-403'd (new transport)

```bash
.venv/bin/python -m indtrader.cli news
```

Expect `transport=curl_cffi` on the fetch line and 100+ items across queries.
If instead transport=urllib and every query is unreachable:
`.venv/bin/pip install curl_cffi` then re-run — and say so in the paste.

>>> PASTE 1 — the fetch line + triage block:
```
...paste...
```

---

## 2. The nightly loop, dry run — takes SECONDS now (this starts the Phase-4 clock)

```bash
time .venv/bin/python -c "from indtrader.scheduler.run_daily import run_once; run_once('data/universe.txt')"
```

~300+ symbols should print `fresh ... — skipped` in milliseconds; only genuinely
stale/empty ones fetch. Under ~3 minutes total (news adds ~20 web queries) =
pass. If anything refetches that looks fresh, paste a couple of those lines.

>>> PASTE 2 — the `time` line + any 3 interesting lines:
```
...paste...
```

---

## 3. Holiday forensics, closed with evidence in the store

```bash
.venv/bin/python -m indtrader.cli holidays --refresh-calendar
.venv/bin/python -m indtrader.cli holidays | tail -12
```

The `holiday master: {...rows_stored: 24...}` line proves the offline ledger
saved (24 matches your PASTE-2 count). The drift ledger line should name the
disputed dates: 2026-01-15, 02-19, 03-19, 04-01, 08-26.

>>> PASTE 3 — both outputs (the refresh line + the tail with the ledger):
```
...paste...
```

---

## 4. Corporate actions probe v2 — the shape inventory (the split-factor key)

```bash
.venv/bin/python -m indtrader.cli coractions HDFCBANK
.venv/bin/python -m indtrader.cli coractions HDFCBANK --from-raw
```

The first output is a `probed:` list — one line per method/arg-form with its
payload SHAPE (`empty list`, `dict.data: N rows, keys=[...]`, or the error).
If ANY line shows rows, the `--from-raw` command replays the parser over it.
If EVERY line is empty/error — that whole list is still the answer: it means
the installed lib can't reach corporate actions at all and we pick another
source (no more knocking on this door).

>>> PASTE 4 — both outputs, complete:
```
...paste...
```

---

## What each answer decides

| # | Question it settles |
|---|---------------------|
| 1 | News goes into the nightly loop for real (or needs one pip line) |
| 2 | Phase-4 discipline clock STARTS (nightly run is viable) |
| 3 | Holiday question closed with a permanent offline drift ledger |
| 4 | Split factors: buildable from the lib (shapes land) or a new source goes on the plan |

Done = push this file back. "Didn't run" is a valid answer, a fake paste is not.
