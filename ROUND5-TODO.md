# ROUND 5 — laptop checklist (~15 min + a dry run)

Round 4 was a big day: the store matched the exchange at 10-name scale, the
universe went whole (500/500), the scan runs in 25s, and NSE's own feed is
confirmed as primary. This round closes Phase 1 formally, does forensics on
the phantom trading days, and starts the Phase-4 ETL discipline.

Code changes pulled this round (pushed as `c12bb98` — your pull must include
it or STOP):

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
git log --oneline -1   # must be c12bb98 or newer
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
...paste...
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
...paste...
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
...paste...
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
...paste...
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
...paste...
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

Done = push this file back. "Didn't run" is a valid answer, a fake paste is not.
