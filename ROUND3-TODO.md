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

Round 2 got ~33/500 symbols. Honest timing: re-runs are SAFE (upserts, no
duplicates) but not fast — each symbol re-downloads its history from 2015
(~15 s each, so the full file is ~2 h end to end). Let it run to completion once;
if you must stop, paste what printed and note roughly how far it got — the next
run continues where the data is missing, it just re-checks from the start.

```bash
.venv/bin/python -m indtrader.cli fetch-universe --file data/universe.txt
.venv/bin/python -m indtrader.cli fetch-adj --file data/universe.txt
.venv/bin/python -m indtrader.cli health --file data/universe.txt
```

If you have to stop mid-way, paste what printed and note roughly how many symbols
it did — partial is usable, next round continues.

>>> PASTE 1 — all three outputs (tail is fine if fetch-universe scrolls forever):
```
...paste...
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
...paste...
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
...paste...
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
...paste...
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
...paste...
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
...paste...
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
...paste...
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
...paste...
```
