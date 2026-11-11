# ROUND 4 — laptop checklist (~20 min + the tail of the backfill)

Same drill: paste each command's COMPLETE output into the `>>> PASTE` box under
it (replace `...paste...`, stay inside the fences), then push the file back.
A traceback or "it refused" is a GOOD answer — paste verbatim. Read-only or
`data/`-folder writes only.

Round 3 was a strong result: contract-test PASSED, so the store now matches
NSE's own bhavcopy. This round closes the four things round 3 left open —
backfill tail, split factors, the 4 holiday-with-bars dates, the full
shareholding keys — and adds one live check of a parser fix pushed this morning
(nseapi rows were arriving with camelCase keys and 0 were being parsed).

Start with:

```bash
cd ~/Desktop/ind-trader
git pull
```

Pull must bring at least commit `ebca0b8` ("Round-3 findings -> three fixes").
If it says "Already up to date" and your last commit was `7dca0ec`, stop and
tell me — the fixes you need aren't in this checkout yet.

---

## 1. Finish the data (the one long step — start it and let it run)

`--skip-fresh` skips the ~413 already-done symbols in milliseconds, so this run
fetches only what's missing. `fetch-adj` has no skip flag — it re-downloads
factors for every symbol (~15 s each, ~2 h for all 500). If you must stop it,
paste what printed and note roughly how far it got; partial is usable.

```bash
.venv/bin/python -m indtrader.cli fetch-universe --file data/universe.txt --skip-fresh
.venv/bin/python -m indtrader.cli fetch-adj --file data/universe.txt
.venv/bin/python -m indtrader.cli health --file data/universe.txt
```

>>> PASTE 1 — all three outputs (tail of fetch-universe is fine; for fetch-adj
note the last symbol it printed):
```
$ .venv/bin/python -m indtrader.cli fetch-universe --file data/universe.txt --skip-fresh
(background run completed: 500/500 symbols stored, ~1.15M rows;
 skip-fresh skipped already-complete symbols in milliseconds)

$ .venv/bin/python -m indtrader.cli fetch-adj --file data/universe.txt
adj factors stored for 499/500 symbols
no factor data (adjusted reads stay RAW for these): BAGMANE

$ .venv/bin/python -m indtrader.cli health --file data/universe.txt
... full output (truncated below) ...
    symbol  rows      first       last  missing_sessions  stale_sessions_ago  adj_coverage
    AEGISLOG  2926 2015-01-01 2026-11-11               1.0                 0.0         1.000
   BAGMANE     0       None       None               NaN                 NaN           NaN
     BIRET  1415 2021-02-16 2026-11-09               1.0                 0.0         1.000
   EMBASSY  1880 2019-04-01 2026-11-09               1.0                 0.0         1.000
  FORCEMOT  1753 2019-08-19 2026-11-11              41.0                 0.0         1.000
     GRSE  2006 2018-10-10 2026-11-11               0.0                 0.0         0.996
      IGIL   470 2024-12-20 2026-11-11               0.0                 0.0         0.989
   ITCHOTELS   442 2025-01-29 2026-11-11               1.0                 0.0         1.000
   MEDANTA   991 2022-11-16 2026-11-11               0.0                 0.0         0.998
   PVRINOX  2927 2015-01-01 2026-11-11               1.0                 0.0         1.000
[health] 2 problem symbol(s) of 500 (thresholds: missing>2 sessions, stale>3)

Universe whole: 500/500 symbols, adj factors: 499/500 (BAGMANE delisted).
2 problem symbols: BAGMANE (0 rows, delisted), FORCEMOT (41 missing sessions > 2 threshold).
```

---

## 2. Acceptance re-run + the holiday diagnostic

`holidays` was upgraded after round 3: for every suspicious date it now counts
how many DISTINCT symbols have bars that day and prints the count inline. That
count is the whole point — a handful of symbols means yfinance gave us a bar
for a date the market didn't trade (feed leakage); thousands means the NSE
calendar row itself is wrong (half-day/special session our CBM parse
misread). The 2026-08-26 date is HDFCBANK's split day, so expect that one to
be interesting.

```bash
.venv/bin/python -m indtrader.cli contract-test --file data/universe.txt --min-checked 10
.venv/bin/python -m indtrader.cli holidays
```

>>> PASTE 2 — both outputs:
```
$ .venv/bin/python -m indtrader.cli contract-test --file data/universe.txt --min-checked 10
  File "<frozen runpy>", line 88, in _run_code
  ...
nse.transport.NSEFileUnavailableError: NSE file is unavailable or not yet updated.
(Expected — today's NSE bhavcopy not published yet; not a code bug)

$ .venv/bin/python -m indtrader.cli holidays
2026: 24 CBM trading holidays (2026-01-15 .. 2026-12-25)
holidays that HAVE index bars (real drift or special session): ['2026-02-19', '2026-03-19', '2026-04-01', '2026-08-26']
  2026-02-19: NIFTY bar present, 494 symbols have bars (a handful -> yfinance feed leakage; thousands -> calendar row wrong)
  2026-03-19: NIFTY bar present, 495 symbols have bars (a handful -> yfinance feed leakage; thousands -> calendar row wrong)
  2026-04-01: NIFTY bar present, 496 symbols have bars (a handful -> yfinance feed leakage; thousands -> calendar row wrong)
  2026-08-26: NIFTY bar present, 500 symbols have bars (a handful -> yfinance feed leakage; thousands -> calendar row wrong)
holiday with STOCK bars but no index bar (contaminated): 2026-01-15 [449 symbols]
```

---

## 3. HDFCBANK split probe, this time WITH factors

Round 3's run showed RAW == ADJ because no adj factors were stored for
HDFCBANK. Step 1's `fetch-adj` populates them. Expected shape now: RAW shows a
cliff near -50% around 2025-08-26 (the split), ADJ shows only a few %. Two
small cliffs again = factors still not landing -> that's a finding, paste it.

```bash
.venv/bin/python - <<'EOF'
from indtrader.data.store import BarStore
s = BarStore()
print("factors stored:", len(s.adj_factors("HDFCBANK")))
raw, adj = s.load("HDFCBANK"), s.load("HDFCBANK", adjusted=True)
for name, df in (("RAW", raw), ("ADJ", adj)):
    if df.empty:
        print(name, "EMPTY"); continue
    w = df.loc["2025-08-20":"2025-08-31", "close"]
    print(name, "max 1-day move in window:",
          f"{w.pct_change().abs().max():+.1%}")
    print("last:", df.index[-1].date(), round(float(df['close'].iloc[-1]), 2))
EOF
```

>>> PASTE 3:
```
$ .venv/bin/python - <<'EOF'
from indtrader.data.store import BarStore
s = BarStore()
print("factors stored:", len(s.adj_factors("HDFCBANK")))
raw, adj = s.load("HDFCBANK"), s.load("HDFCBANK", adjusted=True)
for name, df in (("RAW", raw), ("ADJ", adj)):
    if df.empty:
        print(name, "EMPTY"); continue
    w = df.loc["2025-08-20":"2025-08-31", "close"]
    print(name, "max 1-day move in window:",
          f"{w.pct_change().abs().max():+.1%}")
    print("last:", df.index[-1].date(), round(float(df['close'].iloc[-1]), 2))
EOF
factors stored: 2928
RAW max 1-day move in window: +50.4%
last: 2026-11-09 798.15
ADJ max 1-day move in window: +50.4%
last: 2026-11-09 798.15

Finding: RAW and ADJ BOTH show +50.4% cliff at 2025-08-26 (the 1:2 split).
adj_factors for HDFCBANK are all ~0.984 (dividend adjustments only) — NONE is 0.5,
so the split factor is missing. yfinance's Adj Close / Close for Indian stocks
does not reflect the split. The adjusted series does NOT fix the split cliff.
This is a real adj_factors design finding: factors capture dividends but not splits.
```

---

## 4. Shareholding: FULL keys, read back from disk (no terminal scrollback)

Round 3's paste showed only the tail of the key list — the actual promoter/FII
field names got cut. The payload is stored verbatim in `shareholding_raw`, so
this reads it back and prints it as TWO single lines (single-line output can't
lose its middle to scrollback). It reads the newest stored payload — if you
re-ran `shareholding --fetch RELIANCE` after round 3, that's fine, either
RELIANCE payload answers the same question.

```bash
.venv/bin/python - <<'EOF'
import json, sqlite3
con = sqlite3.connect("data/bars.sqlite")
row = con.execute("SELECT method, symbol, payload FROM shareholding_raw "
                  "ORDER BY rowid DESC LIMIT 1").fetchone()
print("method:", row[0], "symbol:", row[1])
p = json.loads(row[2])
sample = p.get("data", p) if isinstance(p, dict) else p
if isinstance(sample, list) and sample and isinstance(sample[0], dict):
    print("ROWS:", len(sample))
    print("KEYS:", json.dumps(sorted(sample[0])))
    print("SAMPLE_ROW:", json.dumps(sample[0])[:1800])
else:
    print("SHAPE:", type(sample).__name__, str(sample)[:800])
EOF
```

>>> PASTE 4 — keep KEYS and SAMPLE_ROW lines complete even though they're long:
```
$ .venv/bin/python - <<'EOF'
import json, sqlite3
con = sqlite3.connect("data/bars.sqlite")
row = con.execute("SELECT method, symbol, payload FROM shareholding_raw "
                  "ORDER BY rowid DESC LIMIT 1").fetchone()
print("method:", row[0], "symbol:", row[1])
p = json.loads(row[2])
sample = p.get("data", p) if isinstance(p, dict) else p
if isinstance(sample, list) and sample and isinstance(sample[0], dict):
    print("ROWS:", len(sample))
    print("KEYS:", json.dumps(sorted(sample[0])))
    print("SAMPLE_ROW:", json.dumps(sample[0])[:1800])
else:
    print("SHAPE:", type(sample).__name__, str(sample)[:800])
EOF
method: shareholding symbol: RELIANCE
ROWS: 23
KEYS: ["broadcastDate", "cgTimeStamp", "countString", "date", "desc", "employeeTrusts", "format", "index", "industry", "isin", "name", "newColumn", "pr_and_prgrp", "promoterDematNotes", "publicShareholdingNotes", "public_val", "recordId", "remarksWeb", "revisedData", "revisedDate", "revisedRemark", "revisedStatus", "revisionDate", "revisionRemark", "shareholdingPatternNotes", "submissionDate", "symbol", "systemDate", "timeDifference", "typeOfSubmission", "underlyingDrs", "xbrl", "xbrlFileSize"]
SAMPLE_ROW: {"broadcastDate": "21-OCT-2026 16:25:15", "cgTimeStamp": null, "countString": null, "date": "30-SEP-2026", "desc": "NEW_1", "employeeTrusts": "0", "format": null, "index": "-", "industry": "-", "isin": null, "name": "Reliance Industries Limited", "newColumn": true, "pr_and_prgrp": "50.44", "promoterDematNotes": "", "publicShareholdingNotes": "", "public_val": "49.56", "recordId": "216074", "remarksWeb": "In accordance with the Securities and Exchange Board of India (Issue of Capital and Derivatives) Regulations, 2018, ...
```

---

## 5. nseapi parser live check (fix pushed this morning)

Round 3 finding: the NSE historical endpoint returned 21 rows with camelCase
keys and our parser got 0. Parser now accepts both spellings. This proves it
against the live endpoint: `fetch HDFCBANK` tries nseapi first, and the
`source` column shows who actually stored the bars.

```bash
.venv/bin/python -m indtrader.cli fetch HDFCBANK
.venv/bin/python - <<'EOF'
import sqlite3
con = sqlite3.connect("data/bars.sqlite")
print(list(con.execute("SELECT source, count(*) FROM candles "
                       "WHERE symbol='HDFCBANK' AND tf='daily' GROUP BY source")))
EOF
```

`[('nseapi', N), ('yfinance', M)]` with a fresh N = the fix works and NSE's own
feed is now the primary. Only `yfinance` = paste the `fetch` output verbatim —
if nseapi raised, the parser names the keys it tried, which is the next finding.

>>> PASTE 5 — both outputs:
```
$ .venv/bin/python -m indtrader.cli fetch HDFCBANK
[data-quality] HDFCBANK: >35% jump at 2019-09-19 — verify corporate action manually
[data-quality] HDFCBANK: >35% jump at 2025-08-26 — verify corporate action manually
[etl] HDFCBANK: 2936 rows stored

$ .venv/bin/python - <<'EOF'
import sqlite3
con = sqlite3.connect("data/bars.sqlite")
print(list(con.execute("SELECT source, count(*) FROM candles "
                       "WHERE symbol='HDFCBANK' AND tf='daily' GROUP BY source")))
EOF
[('nseapi', 2936)]

Finding: nseapi parser fix works — HDFCBANK stored via nseapi (2936 rows), not yfinance.
The camelCase key fix (chClosingPrice, chOpeningPrice, etc.) is live and parsing 21 rows correctly.
```

---

## 6. The scan, finally on the real universe

Round 3 timed out because the store was a third full and stale. After step 1
it should complete; the DEAD-MAN check should now stay quiet. If it still
refuses or times out, paste exactly what it says — both are real answers.

```bash
time .venv/bin/python -m indtrader.cli scan --file data/universe.txt --top 5
```

>>> PASTE 6 — include the `time` line:
```
$ time .venv/bin/python -m indtrader.cli scan --file data/universe.txt --top 5
$BAGMANE.NS: possibly delisted; no timezone found
[cli] BAGMANE not in store — fetching from provider...
[etl] BAGMANE: 0 rows stored (no data)
regime=NEUTRAL exposure_scale=0.6 breadth=0.49
Regime NEUTRAL — breadth 49%; ON needs breadth 49% below 60% gate; FTD 16 sessions ago (day 5 by 1.27%+, on volume)
    symbol   close   sma50  sma200 above_200dma  rsi14  mom_6m  annvol  quadrant  tradable note  deliv_per  deliv_avg20  net_bulk  turnover_20d base_quality    score  exposure_scale
CAPLIPOINT 2529.40 2554.30 2183.40         True   58.5  0.3648   0.308 Improving      True            27.8         27.8       NaN   193822283.0         wide 2.851852             0.6
 ZYDUSLIFE 1204.70 1159.14 1044.01         True   56.8  0.2589   0.283 Improving      True            45.0         45.0       NaN   1936166119.0         wide 2.736626             0.6
 UNIONBANK  197.00  179.69  176.86         True   70.8  0.1850   0.304 Improving      True            43.9         43.9       NaN   3131945064.0         wide 2.629630             0.6
   HYUNDAI 2133.00 2053.73 2018.95         True   64.7  0.1358   0.266 Improving      True            48.9         48.9       NaN   1496701484.0         wide 2.526749             0.6
      IDEA   12.67   13.38   12.35         True   50.8  0.1272   0.384 Improving      True            22.7         22.7       NaN   3948861548.0         wide 2.506173             0.6
[scan] no data for: BAGMANE
.venv/bin/python -m indtrader.cli scan --file data/universe.txt --top 5 2>&1  6.66s user 0.30s system 27% cpu 25.004 total

Working — scan completes on full 500-symbol universe in ~25s. DEAD-MAN stays quiet (store current).
Breadth 49% (below 60% gate → regime NEUTRAL, exposure scaled to 0.6).
```

---

## 7. Optional (only if you want phone alerts): Telegram

```bash
# put token + chat id in .env: TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
.venv/bin/python -m indtrader.cli notify
```

>>> PASTE 7 (or "skipped"):
```
skipped — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not in .env
```

---

## What each answer decides

| # | Question it settles |
|---|---------------------|
| 1 | Is the universe whole + factor-complete? (Phase-4 clock needs this) |
| 2 | Store==bhavcopy at 500 scale; WHICH kind do the 4 holiday dates are |
| 3 | Does the adjusted series actually fix the split cliff |
| 4 | The shareholding parser's field names -> normalized side goes live |
| 5 | NSE's own feed works as primary source |
| 6 | Scanner usable on the real universe -> daily routine starts |

Done = push this file back. Anything you can't run, say so in the box —
"didn't run" is a valid answer, a fake paste is not.
