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
...paste...
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
...paste...
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
...paste...
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
...paste...
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
...paste...
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
...paste...
```

---

## 7. Optional (only if you want phone alerts): Telegram

```bash
# put token + chat id in .env: TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
.venv/bin/python -m indtrader.cli notify
```

>>> PASTE 7 (or "skipped"):
```
...paste...
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
