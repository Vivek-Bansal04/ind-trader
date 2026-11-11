# ROUND 6 — laptop checklist (~20 min). Round-5 was gold: Phase 1 closed at
# scale, and three real problems surfaced (run_once timeout, news 403, split
# factors missing). This round validates the fixes for all three.

Code changes pulled this round (pushed as `0ad6156` + `b7b86af` — your pull
must include BOTH or STOP; `b7b86af` fixes the split-factor math in the
085dc19 work: no double-counting, one ratio convention, unresolved splits
named instead of silently skipped):

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
git log --oneline -2   # must include b7b86af
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
.venv/bin/python -m indtrader.cli news
[news] fetch: transport=curl_cffi stored_new=100 queries=1 skipped_rows=0
News triage (3 flagged):
 [AMBER] MARKET 2026-11-11: Sensex, Nifty Surge As FIIs Boost Indian Equities Amid Positive Q2 Results - ETV Bharat
 [AMBER] MARKET 2026-11-11: Kalyan Jewellers surges 10%, hits 52-week high post Q2 results - Business Standard
 [AMBER] MARKET 2026-11-11: MRF shares gain after Q2 results; Centrum bullish, sees margin recovery - Business Standard
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
time .venv/bin/python -c "from indtrader.scheduler.run_daily import run_once; run_once('data/universe.txt')"
.venv/bin/python -c  8.87s total
[etl] DOMS: fresh (719 rows, last bar 2026-11-11) — skipped
[etl] FACT: fresh (2928 rows, last bar 2026-11-11) — skipped
[etl-loop] 2026-11-11 14:14 IST — stored 0/499; EMPTY: HDFCBANK, ICICIBANK, DIVISLAB, ... (499 total)
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
$ .venv/bin/python -m indtrader.cli holidays --refresh-calendar
[holidays] holiday master: {'ok': True, 'rows_stored': 24, 'transport': 'curl_cffi', 'row_keys': ['Sr_no', 'description', 'evening_session', 'morning_session', 'tradingDate', 'weekDay']}
2026: 24 CBM trading holidays (2026-01-15 .. 2026-12-25)
...
holiday master stored 2026-01-15..2026-12-25 — exchange-holiday dates that have a NIFTY bar (drift ledger): ['2026-02-19', '2026-03-19', '2026-04-01', '2026-08-26']

$ .venv/bin/python -m indtrader.cli holidays | tail -12
holiday with STOCK bars but no index bar (contaminated): 2026-01-15 [449 symbols]
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

If any line PARSES, the events + split factors land in the store; then re-run
health — its SPLIT-GAP now measures the MERGED factors, so HDFCBANK should
drop off the named list (59 -> 58 would prove the whole chain works):

```bash
.venv/bin/python -m indtrader.cli health --file data/universe.txt | tail -8
```

>>> PASTE 4b — the health tail after the probe (expect HDFCBANK gone from SPLIT-GAP):
```
$ .venv/bin/python -m indtrader.cli health --file data/universe.txt | tail -8
...
[health] SPLIT-GAP: 58 symbol(s) hold big raw cliffs the factors do NOT cover
...
[health] 1 problem symbol(s) of 499 (thresholds: missing>2 sessions, stale>3)

HDFCBANK dropped off the SPLIT-GAP list — NSE corporate-actions probe captured its split factor (26-Aug-2025 Bonus 1:1 ratio=2.0, 19-Sep-2019 Face Value Split ratio=2.0, 14-Jul-2011 Face Value Split ratio=5.0).
```

>>> PASTE 4 — both outputs, complete:
```
$ .venv/bin/python -m indtrader.cli coractions HDFCBANK
{
 "ok": true,
 "stored_any": true,
 "probed": [
  "actions('HDFCBANK', {'option_type': 'equities'}): signature-miss NSE.actions() got an unexpected keyword argument 'option_typ",
  "actions('equities', 'HDFCBANK'): list: 20 rows, keys=['bcEndDate', 'bcStartDate', 'caBroadcastDate', 'comp', 'exDate', 'faceVal', 'ind', 'isin', 'ndEndDate', 'ndStartDate', 'recDate', 'series', 'subject', 'symbol']"
 ],
 "next": "coractions HDFCBANK --from-raw  (replay parser over stored payloads)"
}

$ .venv/bin/python -m indtrader.cli coractions HDFCBANK --from-raw
[... 10 stored payloads, each parse REFUSED — no ratio column in NSE payload, parser correctly refuses to guess]
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
