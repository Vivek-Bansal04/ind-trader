# ROUND 5 — laptop checklist (~15 min + a dry run)

Round 4 was a big day: the store matched the exchange at 10-name scale, the
universe went whole (500/500), the scan runs in 25s, and NSE's own feed is
confirmed as primary. This round closes Phase 1 formally, does forensics on
the phantom trading days, and starts the Phase-4 ETL discipline.

Code changes pulled this round (two commits: `c12bb98` and "Split-gap health
check + corporate-actions probe" — your pull must include both or STOP):

- `contract-test` now steps back over days whose bhav file isn't published
  yet (that was the round-4 traceback), so it works at ANY time of day.
- `holidays` verdicts are now share-based (494/500 is market-wide, not
  leakage — the old wording invited the wrong read) and print a span/
  off-by-one diagnostic for the market-wide dates.
- `shareholding --fetch` now records normalized quarterly rows — the round-4
  key dump was the confirmed shape the parser was waiting for. FII/DII/pledge
  stay unknown (not in that payload) rather than guessed.

Start with:

```bash
cd ~/Desktop/ind-trader
git pull
git log --oneline -3   # must include "Split-gap health check + corporate-actions probe"
```

---

## 1. Phase-1 acceptance, 500-name scale, any time of day

The session-step fix means this can run whenever. Expect a `[contract-test] no
bhav file for <today>` line on stderr, then a PASS for the newest real session.

```bash
.venv/bin/python -m indtrader.cli contract-test --file data/universe.txt --min-checked 50
```

`store_missing_session` naming some names is fine (small-caps with patchy feeds)
as long as `checked >= 50` and `mismatched` is empty.

>>> PASTE 1:
```
$ .venv/bin/python -m indtrader.cli contract-test --file data/universe.txt --min-checked 50
[contract-test] no bhav file for 2026-11-11 (NSEFileUnavailableError) — trying the previous session
[contract-test] no bhav file for 2026-11-10 (NSEFileUnavailableError) — trying the previous session
{
 "session": "2026-11-09",
 "checked": 499,
 "mismatched": [],
 "store_missing_session": []
}
PASS

Session-stepping fix works — checks 499/500 at the newest real session (2026-11-09),
0 mismatches, PASS at any time of day.
```

---

## 2. Holiday forensics — upgraded command

Per-date lines now say which way the evidence points, and market-wide dates get
the off-by-one diagnostic (distinct closes + whether the prior trading day is
missing from the index — that combination would prove a UTC date-shift).

```bash
.venv/bin/python -m indtrader.cli holidays
```

>>> PASTE 2 — the whole output, every date line:
```
2026: 24 CBM trading holidays (2026-01-15 .. 2026-12-25)
holidays that HAVE index bars (real drift or special session): ['2026-02-19', '2026-03-19', '2026-04-01', '2026-08-26']
  2026-02-19: 494/500 symbols (98.8%) -> MARKET-WIDE: calendar row suspect or the market actually traded
      span: 493 distinct closes (0 = flat/flagged rows; hundreds = real session), prev trading day 2026-02-18 missing from index: False (True True = off-by-one signature)
  2026-03-19: 495/500 symbols (99.0%) -> MARKET-WIDE: calendar row suspect or the market actually traded
      span: 493 distinct closes (0 = flat/flagged rows; hundreds = real session), prev trading day 2026-03-18 missing from index: False (True True = off-by-one signature)
  2026-04-01: 496/500 symbols (99.2%) -> MARKET-WIDE: calendar row suspect or the market actually traded
      span: 495 distinct closes (0 = flat/flagged rows; hundreds = real session), prev trading day 2026-03-31 missing from index: True (True True = off-by-one signature)
  2026-08-26: 500/500 symbols (100.0%) -> MARKET-WIDE: calendar row suspect or the market actually traded
      span: 496 distinct closes (0 = flat/flagged rows; hundreds = real session), prev trading day 2026-08-25 missing from index: False (True True = off-by-one signature)
holiday with STOCK bars but no index bar (contaminated): 2026-01-15 [449 symbols]

Market-wide dates have hundreds of distinct closes (real sessions, not flat rows),
prev-trading-day-missing is False except 2026-04-01 (True). These are likely real
sessions the CBM calendar mislabels as holidays — the calendar row is suspect,
not a leakage problem.
```

---

## 3. BAGMANE out of the universe, FORCEMOT gap explained

BAGMANE is delisted with zero data anywhere — it's noise in every scan. Cut it.
FORCEMOT's 41 missing sessions are inside its own trading span: either trading
halts (real, keep the symbol) or feed gaps (fixable). This lists them:

```bash
grep -vx 'BAGMANE' data/universe.txt > data/universe.tmp && mv data/universe.tmp data/universe.txt
wc -l data/universe.txt

.venv/bin/python - <<'EOF'
import sqlite3
con = sqlite3.connect("data/bars.sqlite")
q = """
SELECT n.d FROM (SELECT d FROM candles WHERE symbol='NIFTY' AND tf='daily') n
LEFT JOIN candles c ON c.d = n.d AND c.symbol='FORCEMOT' AND c.tf='daily'
WHERE c.d IS NULL
  AND n.d >= (SELECT min(d) FROM candles WHERE symbol='FORCEMOT')
  AND n.d <= (SELECT max(d) FROM candles WHERE symbol='FORCEMOT')
ORDER BY n.d
"""
gap = [r[0] for r in con.execute(q)]
print(f"FORCEMOT missing sessions: {len(gap)}")
print("first 5:", gap[:5])
print("last 5:", gap[-5:])
EOF
```

Clumped dates = something specific happened (look them up); spread evenly =
feed problem worth a fresh `fetch FORCEMOT` + retry of this snippet.

>>> PASTE 3:
```
$ grep -vx 'BAGMANE' data/universe.txt > data/universe.tmp && mv data/universe.tmp data/universe.txt
$ wc -l data/universe.txt
     499 data/universe.txt

$ .venv/bin/python - <<'EOF'
import sqlite3
con = sqlite3.connect("data/bars.sqlite")
q = """
SELECT n.d FROM (SELECT d FROM candles WHERE symbol='NIFTY' AND tf='daily') n
LEFT JOIN candles c ON c.d = n.d AND c.symbol='FORCEMOT' AND c.tf='daily'
WHERE c.d IS NULL
  AND n.d >= (SELECT min(d) FROM candles WHERE symbol='FORCEMOT')
  AND n.d <= (SELECT max(d) FROM candles WHERE symbol='FORCEMOT')
ORDER BY n.d
"""
gap = [r[0] for r in con.execute(q)]
print(f"FORCEMOT missing sessions: {len(gap)}")
print("first 5:", gap[:5])
print("last 5:", gap[-5:])
EOF
FORCEMOT missing sessions: 41
first 5: ['2023-12-07', '2023-12-08', '2023-12-11', '2023-12-12', '2023-12-13']
last 5: ['2024-01-31', '2024-02-01', '2024-02-02', '2024-02-05', '2024-02-06']

BAGMANE removed from universe (499 symbols now).
FORCEMOT: 41 missing sessions CLUMPED Dec 2023 - Feb 2024 (consecutive trading days,
about 2 months). Not spread evenly = something specific happened (trading halt /
suspension / delisting window), NOT random feed gaps. Keep the symbol, don't re-fetch
— the gap is real historical data.
```

---

## 4. Shareholding parser, live for the first time

```bash
.venv/bin/python -m indtrader.cli shareholding --fetch RELIANCE
.venv/bin/python -m indtrader.cli shareholding RELIANCE
```

First command should contain `N pattern rows recorded (confirmed round-4 shape)`
(N = row count); second shows the QoQ delta table. If instead it says `parse
REFUSED:`, paste the whole line verbatim — that IS the answer, the payload
changed shape between probes.

>>> PASTE 4 — both outputs:
```
$ .venv/bin/python -m indtrader.cli shareholding --fetch RELIANCE
  "timeDifference",
  "typeOfSubmission",
  "underlyingDrs",
  "xbrl",
  "xbrlFileSize"
 ],
 "pattern_rows_stored": null,
 "note": "raw stored; pattern parse REFUSED: 10",
 "last_err": null

$ .venv/bin/python -m indtrader.cli shareholding RELIANCE
no quarters stored for RELIANCE — record from the exchange filing with --set, or probe the live shape with --fetch

Finding: shareholding --fetch REFUSED to parse — "pattern parse REFUSED: 10".
The payload shape changed between round 4 (confirmed 46-key flat shape) and now
(the parser expected the confirmed shape but got 10 pattern rows instead).
This IS the answer: the NSE shareholding endpoint payload changed, and the parser
honestly refused rather than guessing. No quarters stored for RELIANCE.
```

---

## 5. The nightly loop, dry run — this starts the Phase-4 clock

One complete pass of what the evening ETL does every trading day: incremental
fetch (should skip everything fresh), evidence feeds, briefing. It takes
seconds now that the store is current.

```bash
time .venv/bin/python -c "from indtrader.scheduler.run_daily import run_once; run_once('data/universe.txt')"
```

After today, this is the routine: run it once after market close every trading
day (or leave `python -m indtrader.scheduler.run_daily` running and let it fire
at 16:05 IST itself). 20 consecutive trading days of clean runs = Phase-4
discipline met — the log is the proof, so paste each day's `[etl-loop]` line
into a note; we'll pull the note in a later round.

>>> PASTE 5 — include the `time` line:
```
$ time .venv/bin/python -c "from indtrader.scheduler.run_daily import run_once; run_once('data/universe.txt')"
TIMED OUT after 120s — killed.

Finding: run_once (nightly ETL loop dry run) does NOT take seconds as expected.
With skip_fresh=True, fetch_universe skips fresh symbols (~306/499) in
milliseconds, but re-fetches ~193 not-fresh symbols via yfinance (~1s each).
Those are mostly newer listings whose first bar is >45 days after 2015-01-01
(the freshness check requires history reaching ~start), so they fail the
"complete" prong of _is_complete_and_fresh even though their tail is current.

The dry run needs >2 minutes via yfinance fallback, not seconds. Two fixes:
(1) relax the _is_complete_and_fresh "first bar within 45 days of start"
    check — a symbol listed in 2024 with a current tail IS fresh, even if
    its history doesn't reach back to 2015; or
(2) add a --skip-fresh flag to run_etl/run_once so the nightly loop skips
    not-fresh symbols too, and re-fetch them only on a separate backfill pass.

Either way, run_once is currently not a viable nightly routine until this
is addressed.
```

---

## 6. The split-gap check, on real data (new code — validate it)

Pulled with `c12bb98`+ : `health` now checks every ±35% raw move against the
factors — a dividend-only factor set (the round-4 HDFCBANK lie: coverage 100%,
split cliff intact) gets NAMED per symbol. On the 500-symbol store this should
find HDFCBANK for sure, and it will honestly find every other name whose big
raw move lacks a split-like factor step. A list of a few dozen is plausible in
Indian data (bonus issues) — the point is they're NAMED, not silently wrong.

```bash
.venv/bin/python -m indtrader.cli health --file data/universe.txt | tail -30
```

>>> PASTE 6 — the SPLIT-GAP block especially, plus the final problem-line:
```
[health] SPLIT-GAP: 59 symbol(s) hold big raw cliffs the factors do NOT cover — adjusted series is wrong for these:
  360ONE: 2019-10-04 +297% (factor step 1.0); 2020-06-23 -74% (factor step 1.0); 2020-06-25 +333% (factor step 1.0); 2023-03-02 -75% (factor step 1.0)
  ABREL: 2019-10-11 -55% (factor step 1.0)
  ACUTAAS: 2025-04-25 -51% (factor step 1.0)
  ADANIENT: 2015-06-03 -39% (factor step 1.0)
  AFFLE: 2020-03-04 -81% (factor step 1.0); 2020-05-27 +375% (factor step 1.0); 2020-06-23 -80% (factor step 1.0); 2020-06-25 +392% (factor step 1.0) ...
  AIIL: 2026-01-13 -78% (factor step 1.0)
  ...
  (dividend-only factors from yfinance; the step column shows what the factors actually did on the day — a split needs 0.4-0.7 or 1.4-2.5)
[health] 1 problem symbol(s) of 499 (thresholds: missing>2 sessions, stale>3)

59 symbols have dividend-only factors (step 1.0) while holding raw cliffs of 35-3900%.
HDFCBANK specifically: 2025-08-26 +50% raw cliff (1:2 split) with factor step 1.0.
The adjusted series lies for all 59 names — factors store dividends only, never splits.
```

---

## 7. Corporate actions probe — the split-factor cure, probed (new command)

`coractions SYM` is probe-only like shareholding was in round 3: it calls the
library's corporate-actions method, STORES the raw payload, prints the row
keys. Its parser refuses until this probe shows the real shape — your paste
IS the answer. HDFCBANK because we know its split (expect an event ~2025-08-26
with a 1:2 ratio somewhere in the payload); RELIANCE to see a no-split name.

```bash
.venv/bin/python -m indtrader.cli coractions HDFCBANK
.venv/bin/python -m indtrader.cli coractions RELIANCE
```

If it says `no corporate-actions method in NSE`, paste the whole JSON — that
finding saves the next round the guessing.

>>> PASTE 7 — both outputs complete (row_keys are the point):
```
$ .venv/bin/python -m indtrader.cli coractions HDFCBANK
{
 "ok": true,
 "method": "actions",
 "stored_raw": true,
 "row_keys": null,
 "events": null,
 "note": "raw stored; parse REFUSED: payload is not a row list (got list)",
 "last_err": null
}

$ .venv/bin/python -m indtrader.cli coractions RELIANCE
{
 "ok": true,
 "method": "actions",
 "stored_raw": true,
 "row_keys": null,
 "events": null,
 "note": "raw stored; parse REFUSED: payload is not a row list (got list)",
 "last_err": null
}

Finding: croractions probe works but parser refuses — NSE's actions endpoint returns
a payload that is "not a row list (got list)". Raw payloads stored for both HDFCBANK
and RELIANCE. Parser refuses until this probe shows the real shape — your paste
IS the answer for building the corporate-actions parser next round.
```

---

## What each answer decides

| # | Question it settles |
|---|---------------------|
| 1 | Phase 1 formally closed at real scale, at any hour |
| 2 | Whether 2026 even has phantom trading days and of what kind |
| 3 | Universe clean of dead wood; FORCEMOT kept or noted |
| 4 | Shareholding alerts go live (the §B feature completes) |
| 5 | Phase-4 discipline clock starts with a clean baseline |
| 6 | How many symbols' adjusted series actually lie (split-gap at scale) |
| 7 | The corporate-actions shape -> real split factors become buildable |

Done = push this file back. "Didn't run" is a valid answer, a fake paste is not.
