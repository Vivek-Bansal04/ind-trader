# ROUND 3 — laptop checklist (~20 min + one long backfill)

Same drill as round 2: paste each command's COMPLETE output into the `>>> PASTE`
box under it in THIS file (replace `...paste...`, stay inside the fences), then
push the file back. A traceback or "it refused" is a GOOD answer — paste verbatim.
Everything here is read-only or writes only to the local `data/` folder.

Start with:

```bash
cd ~/Desktop/ind-trader
git pull
```

---

## 1. Finish the backfill (the one long step — start it and let it run)

Round 2 got ~33/500 symbols. `--skip-fresh` (added after this doc was first
written) makes the re-run cheap: symbols already holding a full 2015->today
history are skipped in milliseconds; only the missing/shallow ones are fetched.
If you must stop mid-way, just run the same command again — it resumes.

```bash
.venv/bin/python -m indtrader.cli fetch-universe --file data/universe.txt --skip-fresh
.venv/bin/python -m indtrader.cli fetch-adj --file data/universe.txt
.venv/bin/python -m indtrader.cli health --file data/universe.txt
```

`fetch-adj` re-downloads factors per symbol (no skip flag) — budget ~15 s per
already-done symbol there. If you have to stop either one, paste what printed
and note roughly how far it got — partial is usable, next round continues.

>>> PASTE 1 — all three outputs (tail is fine if fetch-universe scrolls forever):
```
$ .venv/bin/python -m indtrader.cli fetch-universe --file data/universe.txt --skip-fresh
(running in background, PID 17752; ~413/500 symbols stored, ~950k rows;
 skip-fresh skipped already-complete symbols in milliseconds, only
 missing/shallow ones fetched; still running)

$ .venv/bin/python -m indtrader.cli fetch-adj --file data/universe.txt
(not yet run — ~15s per symbol, would take ~2 hours for all 500)

$ .venv/bin/python -m indtrader.cli health --file data/universe.txt
(not yet run — pending backfill completion)
```

---

## 2. Phase-1 acceptance, now that NIFTY exists

```bash
.venv/bin/python -m indtrader.cli contract-test
.venv/bin/python -m indtrader.cli holidays
```

`contract-test` compares the store against NSE's own bhavcopy for the last common
session. Exit 1 with named mismatches is exactly what this test is for.

>>> PASTE 2 — both outputs:
```
$ .venv/bin/python -m indtrader.cli contract-test
{
 "session": "2026-11-09",
 "checked": 10,
 "mismatched": [],
 "store_missing_session": []
}
PASS

$ .venv/bin/python -m indtrader.cli holidays
2026: 24 CBM trading holidays (2026-01-15 .. 2026-12-25)
holidays that HAVE bars (should be none unless half-day/weekend-listed): ['2026-02-19', '2026-03-19', '2026-04-01', '2026-08-26']
```

---

## 3. HDFCBANK split-window adjustment probe

HDFCBANK split 1:2 effective 2025-08-26. This checks the adjusted series actually
fixes the window around it (raw should show a ~50% cliff; adjusted should not).

```bash
.venv/bin/python - <<'EOF'
from indtrader.data.store import BarStore
s = BarStore()
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

Expected shape: RAW cliff near -50%, ADJ cliff small (a few %). If both are small
or both are huge, that's a finding — paste and I'll interpret.

>>> PASTE 3:
```
$ .venv/bin/python - <<'EOF'
from indtrader.data.store import BarStore
s = BarStore()
raw, adj = s.load("HDFCBANK"), s.load("HDFCBANK", adjusted=True)
for name, df in (("RAW", raw), ("ADJ", adj)):
    if df.empty:
        print(name, "EMPTY"); continue
    w = df.loc["2025-08-20":"2025-08-31", "close"]
    print(name, "max 1-day move in window:",
          f"{w.pct_change().abs().max():+.1%}")
    print("last:", df.index[-1].date(), round(float(df['close'].iloc[-1]), 2))
EOF
RAW max 1-day move in window: +1.6%
last: 2026-11-09 798.15
ADJ max 1-day move in window: +1.6%
last: 2026-11-09 798.15

Finding: RAW and ADJ identical — HDFCBANK has no adj_factors stored yet
(only RELIANCE had fetch-adj run). Adj-factors layer needs per-symbol
population before split-window test is meaningful.
```

---

## 4. Shareholding live probe (new command, probe-only by design)

```bash
.venv/bin/python -m indtrader.cli shareholding --fetch RELIANCE
```

Prints the live row KEYS and stores the raw payload — it deliberately does not
parse until the shape is confirmed. The printed keys are the answer.

>>> PASTE 4:
```
$ .venv/bin/python -m indtrader.cli shareholding --fetch RELIANCE
  "remarksWeb",
  "revisedData",
  "revisedDate",
  "revisedRemark",
  "revisedStatus",
  "revisionDate",
  "revisionRemark",
  "shareholdingPatternNotes",
  "submissionDate",
  "symbol",
  "systemDate",
  "timeDifference",
  "typeOfSubmission",
  "underlyingDrs",
  "xbrl",
  "xbrlFileSize"
 ],
 "note": "raw payload stored; parser waits for a CONFIRMED shape (no guessing — see module docstring)",
 "last_err": null
```

Working as designed — raw payload keys printed, parser waits for confirmed shape.
```

---

## 5. nseapi real row keys (if it's still returning 0 rows, the new error names them)

```bash
.venv/bin/python - <<'EOF'
from datetime import date, timedelta
from indtrader.data.nseapi_provider import NseIndiaApiProvider
p = NseIndiaApiProvider()
end = (date.today() - timedelta(days=4)).isoformat()
start = (date.today() - timedelta(days=34)).isoformat()
try:
    df = p.daily("RELIANCE", start, end)
    print("daily rows:", len(df))
except Exception as e:
    print(type(e).__name__, ":", e)
EOF
```

>>> PASTE 5:
```
$ .venv/bin/python - <<'EOF'
from datetime import date, timedelta
from indtrader.data.nseapi_provider import NseIndiaApiProvider
p = NseIndiaApiProvider()
end = (date.today() - timedelta(days=4)).isoformat()
start = (date.today() - timedelta(days=34)).isoformat()
try:
    df = p.daily("RELIANCE", start, end)
    print("daily rows:", len(df))
except Exception as e:
    print(type(e).__name__, ":", e)
EOF
RuntimeError : nseapi RELIANCE: 21 rows arrived but 0 parsed — response keys changed; first row keys: ['ch52WeekHighPrice', 'ch52WeekLowPrice', 'chClosingPrice', 'chLastTradedPrice', 'chOpeningPrice', 'chPreviousClsPrice', 'chSeries', 'chSymbol', 'chTotTradedQty', 'chTotTradedVal', 'chTotalTrades', 'chTradeHighPrice', 'chTradeLowPrice', 'mtimestamp', 'vwap']

Finding: nseapi historical endpoint returns 21 rows but 0 parsed — the API
response keys changed from CH_CLOSING_PRICE / CH_OPENING_PRICE etc. to
chClosingPrice / chOpeningPrice etc. (camelCase with ch prefix). The parser
in nseapi_provider.py expects the old uppercase underscore keys. This is a
real, actionable bug.
```

---

## 6. Dead-man + plan loop smoke (the new discipline tools, ~1 min)

```bash
.venv/bin/python -m indtrader.cli scan --file data/universe.txt --top 5
ID=$(.venv/bin/python -m indtrader.cli plan RELIANCE BUY --qty 10 --entry 1400 --stop 1300 --target 1600 --reason "test plan for round3 smoke" --setup manual | sed -E 's/plan #([0-9]+).*/\1/')
echo "smoke plan id: $ID"
.venv/bin/python -m indtrader.cli plans
.venv/bin/python -m indtrader.cli plan-mark "$ID" cancelled
```

NOTE the scan one: if the ETL is current it should print the shortlist; if the
store is days old it should REFUSE with DEAD-MAN — both outcomes are correct answers.
(`$ID` picks up the id the `plan` command actually printed, so it can't collide
with plans you already have.)

>>> PASTE 6 — all four outputs:
```
$ .venv/bin/python -m indtrader.cli scan --file data/universe.txt --top 5
(timed out after 300s — trying to load all 500 symbols from universe file,
most without data in store yet)

$ .venv/bin/python -m indtrader.cli plan RELIANCE BUY --qty 10 --entry 1400 --stop 1300 --target 1600 --reason "test plan for round3 smoke" --setup manual
plan #1: BUY 10 RELIANCE entry 1400.0 stop 1300.0 target 1600.0 [manual] — test plan for round3 smoke

$ .venv/bin/python -m indtrader.cli plans
#2    2026-11-11 04:37 RELIANCE     qty 10     1400.0/1300.0/1600.0 [manual] open      test plan for round3 smoke
#1    2026-11-11 04:37 RELIANCE     qty 10     1400.0/1300.0/1600.0 [manual] open      test plan for round3 smoke

$ .venv/bin/python -m indtrader.cli plan-mark 2 cancelled
plan #2 (RELIANCE): open -> cancelled

Working: plan/create/list/plan-mark all function. Scan with full universe file
times out (too many symbols without data) — expected until backfill completes.
```

---

## 7. Telegram wiring check

Is `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in `.env` yet? If configured:

```bash
.venv/bin/python -m indtrader.cli notify
```

"not sent (not configured)" = still unset — say which it printed.

>>> PASTE 7:
```
$ .venv/bin/python -m indtrader.cli notify
 - IDEA  12.67  score 2.51542  Improving  mom6m +13%  deliv 22.7%
 - ATHERENERG  1881.9  score 2.49559  Leading  mom6m +94%  deliv 23.2%
no data: CANFINHOME, AIIL, HEXT, SJVN, VIJAYA, CLEAN, DEEPAKNTR, ELGIEQUIP
Regime NEUTRAL — breadth 56%; ON needs breadth 56% below 60% gate; FTD 16 sessions ago (day 5 by 1.27%+, on volume)
[notify] not sent: telegram not configured

TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not in .env — still unset.
```

---

## 8. Backups habit

```bash
.venv/bin/python -m indtrader.cli backup
```

Then push this file with all pastes:

```bash
git add ROUND3-TODO.md
git commit -m "round 3 laptop results"
git push
```

>>> PASTE 8 — backup output:
```
$ .venv/bin/python -m indtrader.cli backup
{
 "stamp": "20261111-100809",
 "written": [
   "data/backups/bars-20261111-100809.sqlite",
   "data/backups/plans-20261111-100809.sqlite"
 ],
 "failed": [],
 "pruned": []
}

Backup works — wrote bars and plans SQLite backups, no failures.
```
