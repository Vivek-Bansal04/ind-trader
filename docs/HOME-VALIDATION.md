# Home-laptop validation — the tests this office machine CANNOT run

**How to use this file.** Every block below is copy-paste bash for the MacBook
(`~/Desktop/ind-trader`). Run them in order, then **paste each command's complete
output into the matching `ANSWER n` block IN THIS FILE** (replace the `...paste
output here...` line, keep it inside the fences), and push it back:

```bash
git add docs/HOME-VALIDATION.md
git commit -m "HOME-VALIDATION results from MacBook"
git push
```

Then say the word — I'll pull, read the answers, and fold them into code the same day.
A failure or error trace is a perfectly good answer: paste it verbatim, don't
interpret it. "Errors are data" is the rule here, not a slogan — every endpoint shape
in this project was learned this way, never guessed.

**Why these can't run here:** the office network TLS-intercepts Yahoo (SSL
`WRONG_VERSION_NUMBER`) and 403-blocks NSE. AMFI is reachable (already live-verified
from the office: 14,445 NAVs), so nothing below depends on AMFI.

Prior results received (commit `f54585f`): §0 ran but on a **stale checkout** (147
items — predates the bot + Phase-6 commits) and its 1 failure was an office-side test
bug, now fixed. Its live probe finding (`method: announcements`) is pinned by a test.
**§0 must be re-run on the current checkout, and §1–§7 have never produced output yet.**

---

## 0. Full offline test suite on current checkout [must re-run]

Proves the whole platform (174 tests) is green on macOS + Python 3.13 with zero
network. This gates everything else.

```bash
git pull
.venv/bin/python -m pytest
```

Expected last line: `174 passed, 1 skipped` (the skip is the office-side AMFI live
probe; it only runs with `INDTRADER_LIVE_PROBE=1`). If the count differs, note it —
do not "fix" anything, just report.

```
ANSWER 0:
...paste output here...
```

## 1. Network reachability [why-not-here: both probes are blocked at the office]

```bash
.venv/bin/python tools/net_probe.py
```

Expected at home: `PASS yahoo-chart`, `PASS nse-home`, `PASS yfinance-lib`,
`PASS nseapi-arch` (or one SKIP). Copy ALL lines including the JSON at the end.

```
ANSWER 1:
...paste output here...
```

## 2. Live data end-to-end [needs §1 mostly PASS] — the Phase-1/2 acceptance data

Smoke first (seconds), then the real backfill (minutes; fine to leave running):

```bash
.venv/bin/python -m indtrader.cli fetch RELIANCE
.venv/bin/python -m indtrader.cli backtest RELIANCE --strategy trend_sma200 --costs
.venv/bin/python -m indtrader.cli universe --index "nifty 50"  --out data/universe50.txt
.venv/bin/python -m indtrader.cli universe --index "nifty 500" --out data/universe.txt
.venv/bin/python -m indtrader.cli fetch-universe --file data/universe.txt
.venv/bin/python -m indtrader.cli fetch-feeds --days 30
.venv/bin/python -m indtrader.cli health
```

What each answer decides:
- `backtest RELIANCE` summary block → real costed equity curve vs the synthetic fixtures
- `universe --index "nifty 500"` → whether the NSE-backed universe source works at all
  (if it errors, the traceback IS the answer — the Nifty-500 source is exactly what we need to learn)
- `fetch-universe` row counts per symbol → the 10-year store that backtests need
- `health` → the zero-gap gate vs NIFTY's own bar dates; paste its full output

Paste every command's output (or its error) — one failure does not stop the others.

```
ANSWER 2:
...paste output here...
```

## 3. Bhavcopy contract probe [PROBE — endpoint shape unknown from the office]

The Phase-1 acceptance gate cross-checks our store's closes against NSE's own
bhavcopy — but nobody has SEEN this endpoint's response shape from here. This probe
finds the method and prints the real column contract instead of letting me guess it.
Run after §2 (so the store has rows to compare).

```bash
.venv/bin/python -c "
from indtrader.data.store import BarStore
from indtrader.data.nseapi_provider import NseIndiaApiProvider
n = NseIndiaApiProvider().client()
print('nse lib:', type(n).__module__, type(n).__name__)
meth = [m for m in dir(n) if 'bhav' in m.lower() or 'margin' in m.lower() or 'holiday' in m.lower() or 'scrip' in m.lower()]
print('relevant methods:', meth)
df = None
for m in meth:
    if 'bhav' not in m.lower(): continue
    try:
        df = getattr(n, m)()
        print('called', m, '-> shape', getattr(df, 'shape', type(df).__name__)); break
    except Exception as e:
        print('tried', m, '->', type(e).__name__, str(e)[:160])
if df is not None and hasattr(df, 'columns'):
    cols = {c.upper(): c for c in df.columns}
    symc = next((cols[k] for k in ('SYMBOL',) if k in cols), None)
    px = next((cols[k] for k in ('CLOSE', 'LCLOSE') if k in cols), None)
    cls = next((cols[k] for k in ('SECURITIES_CLASS', 'SECURITY_CLASS') if k in cols), None)
    print('columns:', list(df.columns)[:25])
    if symc and px:
        eq = df[df[cls].astype(str).str.startswith('E')] if cls else df
        s = BarStore()
        for _, r in eq.sample(min(5, len(eq)), random_state=1).iterrows():
            c = s.load(str(r[symc])).close
            print(r[symc], 'bhav', r[px], 'store', float(c.iloc[-1]) if len(c) else None)
    else:
        print('column contract unknown — paste the columns line above; do not let me guess it')
"
```

If every bhav method errors, the printed `relevant methods:` line alone answers what
the adapter must call — paste it.

```
ANSWER 3:
...paste output here...
```

## 4. yfinance Adjusted Close shape [PROBE — Yahoo unreachable from the office]

Decides how `adj_factors` is built (the store's corporate-action adjustment layer
currently only takes identity factors from this machine). SBIN had a 1:2 split in
March 2024 — a known correct answer to check against.

```bash
.venv/bin/python -c "
import yfinance as yf
t = yf.Ticker('SBIN.NS')
h = t.history(start='2024-01-01', end='2024-05-01', auto_adjust=False)
print(h[['Close','Adj Close']].head(3)); print(h[['Close','Adj Close']].tail(3))
print('ratio tail/head:', (h['Adj Close']/h['Close']).iloc[-1] / (h['Adj Close']/h['Close']).iloc[0])
"
```

Expected if usable: ratio near **0.5** across the split (adj/raw diverges). If the
call raises an SSL/HTTP error, that is also a real answer — paste it.

Bonus if it works (one extra command, tells us dividend adjustment is also in there):

```bash
.venv/bin/python -c "
import yfinance as yf
t = yf.Ticker('ITC.NS')   # steady dividend payer, no splits this window
h = t.history(start='2024-01-01', end='2024-12-31', auto_adjust=False)
r = (h['Adj Close']/h['Close']).dropna()
print('ITC ratio first/last:', r.iloc[0], r.iloc[-1], '| steps:', (r.round(6).diff().dropna().abs() > 1e-6).sum())
"
```

```
ANSWER 4:
...paste output here...
```

## 5. NSE library surface: calendar + scrip master [PROBE — same reason]

Two designed-but-unwired features need endpoints nobody can inspect from the office:
the **trading-holiday calendar** (to cross-check NIFTY-bar dates independently) and a
**scrip master** (ISIN → symbol, for bhavcopy hygiene). Dump what the installed
library actually offers and one sample per hit:

```bash
.venv/bin/python -c "
from indtrader.data.nseapi_provider import NseIndiaApiProvider
n = NseIndiaApiProvider().client()
ms = [m for m in dir(n) if not m.startswith('_')]
print('lib:', type(n).__module__, type(n).__name__, '| methods:', len(ms))
print(ms)
for m in ms:
    if any(k in m.lower() for k in ('holiday','trading','scrip','master','corporate','calendar','announc')):
        for args in ((), ('RELIANCE',), ('2026-11-10',)):
            try:
                r = getattr(n, m)(*args)
                print('---', m, args, '->', str(r)[:400]); break
            except TypeError as e:
                last = e
        else:
            print('---', m, 'needs args:', str(last)[:120])
"
```

```
ANSWER 5:
...paste output here...
```

## 6. pypfopt / scipy combination [READY — FOUND BROKEN on the office stack]

`pypfopt 1.6.0` + current scipy: `HRPOpt.optimize()` dies on
`scipy.cluster.hierarchy has no attribute '_LINKAGE_METHODS'`. The code catches it
and falls back to inverse-vol loudly, so nothing is blocked — but if HRP is actually
wanted, find a combination that runs:

```bash
.venv/bin/python -c "from pypfopt import HRPOpt; from pypfopt.risk_models import risk_matrix; print('pypfopt import OK')"
.venv/bin/python -c "
import numpy as np, pandas as pd
from pypfopt import HRPOpt
r = pd.DataFrame(np.random.default_rng(1).normal(0, .02, (300, 4)), columns=list('ABCD'))
print(HRPOpt(r).optimize())
"
```

If the second one throws `_LINKAGE_METHODS`: in a throwaway venv try
`pip install 'scipy<1.15'` (or pin PyPortfolioOpt to a build matching the installed
scipy) and report **which exact combination runs** — that becomes the pin.

```
ANSWER 6:
...paste output here...
```

## 7. Phase-4 paper loop start [after §2] — the calendar commitment begins

One evening, two commands:

```bash
.venv/bin/python -m indtrader.cli scan
.venv/bin/python -m indtrader.cli notify
```

Paste the output (or first errors). Then the build plan's acceptance rule starts
counting, no code involved: **20 trading days of ETL with zero gaps vs bhavcopy**
(measured by `health`), and every paper fill entered with
`fill SYMBOL BUY|SELL QTY PRICE --costs <all-in>` within a day of the real fill.
Fills are human-confirmed records of trades you already made — nothing in this repo
can place an order, and nothing will be added that can.

```
ANSWER 7:
...paste output here...
```

---

## Results table (I fill this from your ANSWER blocks; you can too)

| # | Item | Status (PASS/FAIL/SKIP) | Decides |
|---|---|---|---|
| 0 | offline suite on current checkout | | macOS/3.13 support claim |
| 1 | network reachability | | which providers are usable at home |
| 2 | live fetch/backtest/universe/health | | Phase-1 + Phase-2 acceptance |
| 3 | bhavcopy contract | | the contract-test command + zero-gap gate |
| 4 | yfinance Adj Close shape | | adj_factors final design |
| 5 | NSE calendar/scrip methods | | wire or record-reject two features |
| 6 | pypfopt combo | | pin scipy/pypfopt or keep fallback |
| 7 | paper loop first run | | Phase-4 clock start date |
