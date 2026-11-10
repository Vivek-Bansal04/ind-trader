# ROUND 2 — run top to bottom on the MacBook (~15 min + one long backfill)

Self-contained: every command here can be copy-pasted as-is. Paste each command's
**complete output into the `>>> PASTE` box under it in THIS file** (replace the
`...paste...` line, keep it inside the fences), then push the file back at the end.
Failures and tracebacks are good answers — paste verbatim, don't interpret.

Start with:

```bash
cd ~/Desktop/ind-trader
git pull
```

---

## 1. Finish the data chain (the one long step — start it and let it run)

NIFTY was never fetched last round (it's not in the nifty-500 file), which is the only
reason `health` refused. Run in this order:

```bash
.venv/bin/python -m indtrader.cli fetch NIFTY
.venv/bin/python -m indtrader.cli fetch-universe --file data/universe.txt
.venv/bin/python -m indtrader.cli fetch-adj --file data/universe.txt
.venv/bin/python -m indtrader.cli backtest RELIANCE --strategy trend_sma200 --adjusted --costs
.venv/bin/python -m indtrader.cli health --file data/universe.txt
```

`fetch-universe` over 500 symbols takes a while (yfinance, one by one). If you have to
stop it, paste what printed so far and note how far it got — partial is usable.

>>> PASTE 1 — all five outputs:
```
...paste...
```

## 2. Bhavcopy column contract (30 seconds — one line is the whole point)

Round 1 proved `equity_bhavcopy(date)` works and prices match 10/10. We need the CSV's
**column names** — the `columns:` line — to code the `contract-test` command.

```bash
.venv/bin/python -c "
from datetime import date, timedelta
from indtrader.data.store import BarStore
from indtrader.data.nseapi_provider import NseIndiaApiProvider
n = NseIndiaApiProvider().client()
d = date.today() - timedelta(days=1)
if d.weekday() > 4:
    d -= timedelta(days=d.weekday() - 4)
raw = n.equity_bhavcopy(d)
print('method ok | returned:', type(raw).__name__, '| date:', d)
import pandas as pd
df = raw if hasattr(raw, 'columns') else pd.read_csv(str(raw))
print('file:', raw, '| shape:', getattr(df, 'shape', '?'))
print('columns:', list(df.columns))
cols = {c.upper().strip(): c for c in df.columns}
symc = next((cols[k] for k in ('SYMBOL',) if k in cols), None)
px = next((cols[k] for k in ('CLOSE', 'LCLOSE') if k in cols), None)
cls = next((cols[k] for k in ('SECURITIES_CLASS', 'SECURITY_CLASS') if k in cols), None)
print('mapped -> symbol:', symc, '| close:', px, '| class:', cls)
if symc and px:
    eq = df[df[cls].astype(str).str.startswith('E')] if cls else df
    s = BarStore()
    for _, r in eq.sample(min(10, len(eq)), random_state=1).iterrows():
        c = s.load(str(r[symc])).close
        print(r[symc], 'bhav', r[px], 'store', float(c.iloc[-1]) if len(c) else None)
else:
    print('column contract unknown — paste the columns line above; do not let me guess it')
"
```

If it errors on a weekday, retry with the previous weekday and note which date worked.

>>> PASTE 2:
```
...paste...
```

## 3. NSE library shapes (two short probes)

3a. Holiday/scrip/announcement return shapes — decides whether we wire the trading
calendar or record a rejection:

```bash
.venv/bin/python -c "
from indtrader.data.nseapi_provider import NseIndiaApiProvider
n = NseIndiaApiProvider().client()
ms = [m for m in dir(n) if not m.startswith('_')]
print('lib:', type(n).__module__, type(n).__name__, '| methods:', len(ms))
print(ms)
for m in ms:
    if any(k in m.lower() for k in ('holiday','trading','scrip','master','corporate','calendar','announc')):
        for args in ((), ('RELIANCE',), ('2026-11-11',)):
            try:
                r = getattr(n, m)(*args)
                print('---', m, args, '->', str(r)[:400]); break
            except TypeError as e:
                last = e
        else:
            print('---', m, 'needs args:', str(last)[:120])
"
```

3b. The historical-data call returned **0 rows** last round with `date` objects. This
prints its real signature and tries three argument forms — it decides whether that
provider keeps its fetch-chain slot:

```bash
.venv/bin/python -c "
import inspect
from datetime import date
from indtrader.data.nseapi_provider import NseIndiaApiProvider
p = NseIndiaApiProvider(); n = p.client()
for m in [x for x in dir(n) if 'histor' in x.lower() or 'archive' in x.lower()]:
    try:
        print('---', m, inspect.signature(getattr(n, m)))
    except (TypeError, ValueError) as e:
        print('---', m, 'no signature:', e)
f = [x for x in dir(n) if 'historical' in x.lower()]
if f:
    meth = getattr(n, f[0])
    for label, args in [('date objects', ('RELIANCE', date(2026, 10, 1), date(2026, 10, 15))),
                        ('iso strings', ('RELIANCE', '2026-10-01', '2026-10-15')),
                        ('kwargs iso', None)]:
        try:
            r = meth(*args) if args else meth('RELIANCE', from_date='2026-10-01', to_date='2026-10-15')
            print('OK ', label, '->', type(r).__name__, (len(r) if hasattr(r, '__len__') else r))
            break
        except Exception as e:
            print('ERR', label, '->', type(e).__name__, str(e)[:140])
"
```

>>> PASTE 3a:
```
...paste...
```

>>> PASTE 3b:
```
...paste...
```

## 4. Find a REAL split window for the adj-factor test (optional but useful)

Last round's SBIN premise was wrong (no March-2024 split), so split behavior is still
untested. This survey finds one honestly; if any symbol prints a split inside ~2
years, re-run PASTE-4-style ratio probe over a window spanning that date:

```bash
.venv/bin/python -c "
import yfinance as yf
for s in ('SBIN', 'HDFCBANK', 'ICICIBANK', 'ITC', 'TATASTEEL', 'LT', 'BHARTIARTL',
          'JIO', 'ADANIPORTS', 'MARUTI', 'TITAN', 'ASIANPAINT'):
    try:
        sp = yf.Ticker(s + '.NS').splits
        if sp is not None and len(sp):
            print(s, 'last split:', sp.index[-1].date(), 'ratio', float(sp.iloc[-1]))
    except Exception as e:
        print(s, type(e).__name__, str(e)[:80])
"
```

>>> PASTE 4 (skip if no splits printed — that IS an answer too):
```
...paste...
```

## 5. pypfopt — verbatim outputs this time

```bash
.venv/bin/python -c "import scipy; print('scipy', scipy.__version__)"
.venv/bin/python -c "import pypfopt; print('pypfopt', pypfopt.__version__)"
.venv/bin/python -c "from pypfopt import HRPOpt; print('import OK')"
.venv/bin/python -c "
import numpy as np, pandas as pd
from pypfopt import HRPOpt
r = pd.DataFrame(np.random.default_rng(1).normal(0, .02, (300, 4)), columns=list('ABCD'))
print(HRPOpt(r).optimize())
"
```

If the last one throws `_LINKAGE_METHODS`, try in a THROWAWAY venv (not this one):
`python3 -m venv /tmp/pin && /tmp/pin/bin/python -m pip install 'scipy<1.15'
pyportfolioopt` then rerun the last command with `/tmp/pin/bin/python` — whichever
combo runs, paste it.

>>> PASTE 5:
```
...paste...
```

## 6. scan / notify — FULL traceback this time (do AFTER step 1)

Last round a TypeError was reported but the trace never arrived. With NIFTY + backfill
in the store it may not even reproduce — either way, paste everything:

```bash
.venv/bin/python -m indtrader.cli scan 2>&1
.venv/bin/python -m indtrader.cli notify 2>&1
```

>>> PASTE 6:
```
...paste...
```

## 7. Sanity check (10 seconds)

```bash
.venv/bin/python -m pytest -q
```

>>> PASTE 7 (just the last line):
```
...paste...
```

---

## Push it back

```bash
git add ROUND2-TODO.md
git commit -m "ROUND2 results from MacBook"
git push
```

Then tell me. Next pull I build `contract-test` against the real columns, decide the
nseapi provider's fate, close the Phase-1/2 live acceptance from PASTE 1, and the
Phase-4 paper clock starts on PASTE 6's date.
