# ROUND 10 — the office box built the paper-loop glue, gap-aware batch gate,
# offline ratio resolver — and the My-holdings panel + Telegram watchers — for
# the first time. This round runs them against the REAL store. ROUND8 and
# ROUND9 are still open — do those first if you haven't; nothing here replaces
# them.

Pull first: `git pull --ff-only` — these commands must exist in `--help`.

## 1. Offline ratio sweep (no network — run anywhere, 30 s)

```
indtrader coractions --resolve
```

PASTE 1:
```
coractions --resolve: 0/0 stored-text rows resolved by the CURRENT parser; the rest need human --set (never a guessed ratio)

Finding: 0 split-like rows with NULL ratio in corporate_actions — all split-like rows already have ratios stored. The 1060 rows without ratio are dividends (not split-like, correctly left as NULL). Remaining 65 symbols in health split-gap list have NO row in corporate_actions at all (not even a dividend row), i.e., the NSE API has no corporate-action records for those symbols at all.
```
"0/0 rows" on the laptop is NOT expected — health named ~59 split-gap symbols
and the round-7 parser may now read subjects the old one left ratio-less.
Whatever stays UNRESOLVED is your human `--set` worklist (the output prints
the exact command per row).

## 2. Gap-aware batch gate smoke test (~2 min, not the full 10y run)

```
python -m indtrader.cli gate-universe RELIANCE TCS HDFCBANK INFY ICICIBANK --strategy trend_sma200 --param period=150,200,250 --warmup 150 --test-days 84
```

PASTE 2:
```
$ .venv/bin/python -m indtrader.cli gate-universe RELIANCE TCS HDFCBANK INFY ICICIBANK --strategy trend_sma200 --param period=150,200,250 --warmup 150 --test-days 84
[gate-universe] ICICIBANK: FAIL — no parameter produced trades in the IS window
SKIP     RELIANCE       20 unexplained cliff(s): 2015-02-28 +342% (factor step 1.0); 2015-03-02 -77% (factor step 1.0)
SKIP     TCS            6 unexplained cliff(s): 2015-02-28 +101% (factor step 1.0); 2015-03-02 -50% (factor step 1.0)
NO-DATA  HDFCBANK       load failed: ValueError: cannot reindex on an axis with duplicate labels
SKIP     INFY           6 unexplained cliff(s): 2015-02-28 +305% (factor step 1.0); 2015-03-02 -75% (factor step 1.0)
FAIL     ICICIBANK      no parameter produced trades in the IS window
gate-universe [trend_sma200/sharpe]: 0 pass | 1 fail | 3 excluded pending factors | 1 no data

Finding: HDFCBANK still fails (duplicate labels error — not fixed by ROUND8). 59 symbols remain in split-gap (HDFCBANK + 64 others). Only 2/5 symbols passed gate; RELIANCE and TCS still have unexplained cliffs.
```

## 3. The paper loop end-to-end (evening chain, ~5 min)

```
indtrader scan
indtrader drafts --top 3
indtrader drafts --top 3 --apply
indtrader brief
```

PASTE 3 (four boxes, label which is which):
```
a) last 12 lines of scan
b) drafts printed WITHOUT --apply
c) the stored-plan lines with --apply
d) brief output next morning (it must show the three plans or say they expired)
```

Sanity the code asserts and you can check: drafts never size a symbol already
held/open; a results-blackout name prints a skip reason, not a qty; `brief`
lines quote the LAST CLOSED bar's date, not "today".

## 4. Dashboard redesign — first live look (the page you screenshot today is REPLACED)

```.venv/bin/python -m indtrader.cli dashboard --universe data/universe.txt
```

Open http://127.0.0.1:8765 — the page now leads with a plain-words verdict +
"Your moves today" cards; every technical table is inside a collapsed dropdown.

PASTE 4 (screenshot to `screenshots/`, plus text answers):
- does the verdict line read like something a layman can act on? (yes/no + what confused you)
- open one "why" on a shortlist row — does the arithmetic line add up to the
  shown score (e.g. `+1.5 quadrant + 0.85 momentum rank + 0.50 bulk buying → score 2.85`)?
- does the banner/health line now say ~500 symbols (not 10)?
- are UNRESOLVED dates shown FULL ("07-Sep-2017" style rows from old captures
  must NOT read "07-Sep-201" anymore — they should show the whole string or
  ISO "2017-09-07" for anything stored after today's fix)?
- is the scan score rounded (2 decimals, no 2.8518518…)?
- did HDFCBANK's stale factor rows change? paste `sqlite3 data/bars.sqlite
  "SELECT * FROM split_factors WHERE symbol='HDFCBANK'"` — the ROUND8 factor-step
  4.0 evidence is still needed here.
- NEW in this build: record one real holding via the My-holdings form (a small
  position you actually own) — does the row show your qty, your average, and a
  P&L that matches the broker app? A refusal (bad qty etc.) must print the
  validation's own sentence under the form. Screenshot the panel.

## 5. My-holdings + Telegram watchers — first live run (same build night)

```
indtrader events --save TCS=YYYY-MM-DD   (any real results date you can verify; 2 names = better)
indtrader events TCS --qty 100
indtrader holdings
indtrader holdings --notify
```

```
indtrader events          (the plain fetch — does the live NSE calendar probe find a real method?)
```

PASTE 5 (five boxes):
```
a) events --save output + what date you verified it against (BSE/NSE link)
b) events TCS --qty 100 (the size-gate note must name the date distance)
c) holdings output (qty/avg/P&L must match YOUR broker for at least one real
   stock you own — this is the FIFO-math proof; wrong avg = paste the fills:
   sqlite3 data/plans.sqlite "SELECT * FROM ledger WHERE symbol='SYMBOL'")
d) holdings --notify output. No Telegram configured? The line must say so —
   "nothing sent: telegram not configured (...)" is a PASS, silence is not.
   Configured: you should get exactly one message per due item, and running
   --notify AGAIN must send nothing the second time (dedup).
e) the plain `indtrader events` fetch line — {"ok": true, "method": ...} or
   the honest error; if ok, does the calendar hold real dates? (compare one
   name against the NSE site; a wrong shape is exactly what this round is for)
```

## 6. Backlog reminder (unchanged from ROUND8/9)

- ROUND8 §1: `coractions --gap-list --file data/universe.txt --limit 60` batch
- ROUND8 §2: dashboard screenshot + HDFCBANK factor rows
- ROUND9: tonight's `[etl-loop]` paste block (the Phase-4 clock — one per day)

Done = push this file back. "Didn't run" is a valid answer, a fake paste is not.
