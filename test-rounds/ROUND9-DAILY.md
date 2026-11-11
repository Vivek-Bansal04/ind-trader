> Moved to `test-rounds/` on 2026-11-12 — edit THIS file where it now lives; the
> paste-back workflow (paste into the file, push it back) is unchanged.

# ROUND 9+ — the daily 2 minutes (no round number after this — this file IS
# the rhythm). Phase 4 closes after 20 consecutive trading days whose nightly
# `[etl-loop]` line is clean; this file is how we see that from here without
# babysitting. Do it AFTER the nightly loop has run (loop fires 16:05 IST
# weekday; this paste is for ~16:20 or anytime after, still valid next morning
# — but one genuine paste per trading day, copy-pasting an old block is the
# fake answer this repo refuses).

Prereq once:

```bash
cd ~/Desktop/ind-trader
git pull
git log --oneline -3   # must include ae6a1ab (the dashboard round)
```

The nightly loop must be alive on this machine — either
`docker compose up -d etl-loop` (if you did ROUND8 §3) or the classic:

```bash
mkdir -p logs   # once, if logs/ doesn't exist
nohup .venv/bin/python -m indtrader.scheduler.run_daily >> logs/etl.log 2>&1 &
```

(`pgrep -f run_daily` prints a number = alive. If it died, just start it
again — upserts are idempotent, a missed night self-heals next evening.)

---

## Every trading evening (Mon-Fri, after 16:15 IST)

```bash
.venv/bin/python -m indtrader.cli coractions --gap-list --file data/universe.txt 2>&1 | tail -25   # skip this line until ROUND8 §1 is pasted
.venv/bin/python -m indtrader.cli health --file data/universe.txt | tail -8
sqlite3 data/bars.sqlite "SELECT 'nifty_last', max(d) FROM candles WHERE symbol='NIFTY' AND tf='daily'"
```

Paste into today's block (add a new one each day, newest on top; the NIFTY
date must have ADVANCED vs yesterday — that single fact is the streak):

### 2026-11-12 (Thu)
```
```

### 2026-11-13 (Fri)
```
```

### 2026-11-16 (Mon)
```
```

### 2026-11-17 (Tue)
```
```

### 2026-11-18 (Wed)
```
```

### 2026-11-19 (Thu)
```
```

### 2026-11-20 (Fri)
```
```

### 2026-11-23 (Mon)
```
```

### 2026-11-24 (Tue)
```
```

### 2026-11-25 (Wed)
```
```

### 2026-11-26 (Thu)
```
```

### 2026-11-27 (Fri)
```
```

### 2026-11-30 (Mon)
```
```

### 2026-12-01 (Tue)
```
```

### 2026-12-02 (Wed)
```
```

### 2026-12-03 (Thu)
```
```

### 2026-12-04 (Fri)
```
```

### 2026-12-07 (Mon)
```
```

### 2026-12-08 (Tue)
```
```

### 2026-12-09 (Wed)
```
```

### 2026-12-10 (Thu)
```
```

---

## Rules that keep the streak honest

- A day counts when: the loop ran AND `nifty_last` moved to that day (or the
  exchange was actually closed — I check that against the holiday master from
  here, you don't judge it).
- A gap or a crash line: leave the paste as-is and push; naming the day is
  the cure, restart the loop, the streak counter restarts — no editorialising.
- Skipped days: write `SKIPPED: <reason>` in the block. Also valid, also honest.
- Push whenever — one commit per evening or a batch every few days, both fine.
  Just never re-fill a past day from memory.

## Where we are

ROUND8 (§1-3) still open — its PASTE 1 is the batch that shrinks the SPLIT-GAP
list; keep its `--gap-list` line in the evening routine until that number
hits zero or the remainder is explicitly moved to `--set` from exchange PDFs.
The day it's quiet, delete that line from the routine.

20 clean days here = Phase-4 close. The dashboard (ae6a1ab) shows the same
freshness picture from the same DB whenever you want it:
`.venv/bin/python -m indtrader.cli dashboard` → http://127.0.0.1:8765
