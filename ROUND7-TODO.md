# ROUND 7 — laptop checklist (~15 min). Round 6 closed three things: news
# works over curl_cffi, the nightly loop runs in 9 seconds, and the holiday
# drift ledger matches to the date. This round closes the split factor — the
# CA payload shape landed (20 HDFCBANK rows), and the parser now reads it; we
# just need to see the real subject texts to know if ratios are stated.

Code changes pulled this round (`1f9b980` + `49efa1e` — pull must include
both or STOP):

- `coractions` parses the CONFIRMED payload (exDate/subject/faceVal keys).
  A ratio is stored ONLY when the subject states numbers; split-looking rows
  without numbers are stored and NAMED as UNRESOLVED — no guessing, and
  health keeps naming the symbol meanwhile.
- Freshness needs a SETTLED tail: today's (pre-16:15: also yesterday's) bar
  is refetched, because the round-6 dry run at 14:14 IST froze mid-session
  bars as closes. Heads-up: the FIRST run_once after this pull refetches
  symbols whose last bar is today-stamped — expected, it's the cure.
- run_once's loop line now distinguishes `fresh-skipped` from real `EMPTY`
  (the old line printed "EMPTY: <all 499>" while every symbol was skipped —
  a false alarm by construction).

Start with:

```bash
cd ~/Desktop/ind-trader
git pull
git log --oneline -2   # must include 49efa1e
```

---

## 1. The subjects, verbatim — this is the round

```bash
.venv/bin/python -m indtrader.cli coractions HDFCBANK
.venv/bin/python -m indtrader.cli coractions HDFCBANK --from-raw
```

The parse note will say `... N split factors; UNRESOLVED M: <subjects>`.
Those UNRESOLVED strings are the exact wording NSE uses for HDFCBANK's
splits/bonus — they decide everything: if a ratio hides in them in a
spelling my parser doesn't read, this paste teaches it; if they truly
carry no ratio, we source ratios from the exchange PDF and stop knocking
on this door. Also: if the note shows a TypeError/exception instead of a
parse note, paste the whole thing verbatim.

>>> PASTE 1 — both outputs complete (the note lines are the point):
```
...paste...
```

---

## 2. What health says with the merged factors

```bash
.venv/bin/python -m indtrader.cli health --file data/universe.txt | tail -8
```

If step 1 left events UNRESOLVED, HDFCBANK is EXPECTED to be back on the
SPLIT-GAP list — that's the fail-loud rule working, not a regression.

>>> PASTE 2 — the tail:
```
...paste...
```

---

## 3. run_once, settled-bars edition (the real nightly shape)

```bash
time .venv/bin/python -c "from indtrader.scheduler.run_daily import run_once; run_once('data/universe.txt')"
```

First pass after the pull: expect a one-time refetch of the today-stamped
symbols (could be most of the 499 — nseapi is fast, ~1-3 min is fine), and
a final line like `stored X/499, fresh-skipped N` with NO giant EMPTY list.

>>> PASTE 3 — the `time` line + the final `[etl-loop]` line:
```
...paste...
```

---

## What each answer decides

| # | Question it settles |
|---|---------------------|
| 1 | Split ratios: stated in NSE text (parser gets the spelling) or not (source changes) |
| 2 | The 58 remaining split-gap names — same missing-ratio story, or different? |
| 3 | Phase-4 nightly routine, verified end-to-end on settled data |

Done = push this file back. "Didn't run" is a valid answer, a fake paste is not.
