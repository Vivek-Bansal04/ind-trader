# Home-laptop validation TODO (things this office machine cannot do)

The office laptop TLS-intercepts Yahoo (SSL `WRONG_VERSION_NUMBER`) and 403-blocks NSE,
so every **live-data** and **endpoint-shape** check is parked here. Run these on the
personal laptop (any network that isn't the office proxy), fill the `ANSWER:` lines or
paste outputs back in chat — I'll fold the results into code the same day.

Legend: **[READY]** = command exists at the commit you're on · **[PROBE]** = exploratory,
just capture the output · **[PENDING CODE]** = will be a real command in a later push;
skip if it doesn't exist yet.

> Update 2026-11-10: **AMFI (MF NAVs) is reachable from the office too** — the Phase-6
> MF layer (`mf-navs`) was already live-verified here (14,445 NAVs, contract green).
> Nothing in this checklist depends on it; it stays for the NSE/Yahoo items only.

> **Your laptop is the MacBook (macOS)** — every block below is bash (`.venv/bin/python`).
> On a Windows box the translation is the reverse: `.venv\Scripts\python.exe` + `\` paths.
>
> Results received (commit `f54585f`): §0 ran on an **older checkout** — 147 items means
> the log predates the bot + Phase-6 commits. Its 1 failure was a test hardcoding "nse
> lib not installed"; now fixed to pass either way, and your live `method: announcements`
> finding is pinned by a new probe test. Your `NSE(download_folder=...)` fix is kept and
> the same bug in `events.py` was fixed too. **Please `git pull` and re-run §0** (expect
> ~175 tests), then fill the still-empty ANSWER slots §1–§6 — the §3/§4/§5/§6 snippets
> below are written for the newer `nse.NSE` library your machine has.

---

## 0. Setup (one time)

macOS/Linux (your MacBook):

```bash
git clone https://github.com/Vivek-Bansal04/ind-trader.git
cd ind-trader
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,nse]"
.venv/bin/python -m pytest
```

(On Windows keep `python -m pip` — the `pip.exe` shim is blocked on the office box only.)

Expected: all tests pass (~175), fully offline.

`ANSWER 0:` pytest last line → ______

## 1. Network reachability [READY]

```bash
.venv/bin/python tools/net_probe.py
```

Expected at home: `PASS yahoo-chart`, `PASS nse-home`, `PASS yfinance-lib`,
`PASS nseapi-arch` (or one SKIP). Copy all lines + the JSON.

`ANSWER 1:` ______

## 2. Real data end-to-end [READY] — needs ANSWER 1 mostly PASS

Small smoke first (seconds), then the Phase-1 real thing (minutes, run overnight if slow):

```bash
.venv/bin/python -m indtrader.cli fetch RELIANCE
.venv/bin/python -m indtrader.cli backtest RELIANCE --strategy trend_sma200 --costs

# universe from NSE itself (this needs the network):
.venv/bin/python -m indtrader.cli universe --index "nifty 50"  --out data/universe50.txt
.venv/bin/python -m indtrader.cli universe --index "nifty 500" --out data/universe.txt   # if this errors, note the error - Nifty-500 source is exactly what we need to learn
# 10-year backfill + evidence feeds:
.venv/bin/python -m indtrader.cli fetch-universe --file data/universe.txt
.venv/bin/python -m indtrader.cli fetch-feeds --days 30
```

Paste the `backtest` summary block and any errors verbatim (failures are data too).

`ANSWER 2:` ______

## 3. Bhavcopy contract test [PROBE] — cross-check vs NSE's own numbers

`NseIndiaApiProvider.client()` opens the library client with the `download_folder`
ritual handled (your laptop proved why that matters). It needs the NEW-generation
library (`pip install "indtrader[nse]"`); if `from nse import NSE` fails on an old
install, paste the fallback method dump at the end of this section instead. Run after
§2 and paste the whole output:

```bash
.venv/bin/python -c "
import tempfile
from indtrader.data.store import BarStore
from indtrader.data.nseapi_provider import NseIndiaApiProvider
p = NseIndiaApiProvider(); n = p.client()
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

If every bhav method errors, paste the printed method list instead — that alone answers
what the adapter must call.

`ANSWER 3:` ______

## 4. yfinance Adjusted Close shape [PROBE] — feeds the corporate-action factor design

```bash
.venv/bin/python -c "
import yfinance as yf
t = yf.Ticker('SBIN.NS')   # SBIN had a 1:2 split in 2024 — good test case
h = t.history(start='2024-01-01', end='2024-05-01', auto_adjust=False)
print(h[['Close','Adj Close']].head(3)); print(h[['Close','Adj Close']].tail(3))
print('ratio tail/head:', (h['Adj Close']/h['Close']).iloc[-1] / (h['Adj Close']/h['Close']).iloc[0])
"
```

Expected if usable: a ratio near 0.5 across the March-2024 split (adj/raw diverges).
Paste all of it — this decides how we build `adj_factors`.

`ANSWER 4:` ______

## 5. NSE API surface for calendar + scrip master [PROBE]

Two Phase-1/2 items need endpoints I can't inspect from the office box (calendar table,
ISIN/scrip master). Dump what the installed library actually offers + one sample each:

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

`ANSWER 5:` ______

## 6. pypfopt on this stack [READY] — FOUND BROKEN on the office box (2026-11-10)

`pypfopt 1.6.0` + current scipy: `HRPOpt.optimize()` dies on
`scipy.cluster.hierarchy has no attribute '_LINKAGE_METHODS'`. The code now catches
this and falls back to inverse_vol with a loud message, so nothing is blocked — but
if HRP is actually wanted, test whether an older combo works on the laptop:

```bash
.venv/bin/python -c "from pypfopt import HRPOpt; from pypfopt.risk_models import risk_matrix; print('pypfopt import OK')"
.venv/bin/python -c "
import numpy as np, pandas as pd
from pypfopt import HRPOpt
r = pd.DataFrame(np.random.default_rng(1).normal(0, .02, (300, 4)), columns=list('ABCD'))
print(HRPOpt(r).optimize())"   # if this throws _LINKAGE_METHODS, HRP is dead on this scipy
```
If dead: try `.venv/bin/python -m pip install 'scipy<1.15'` in a scratch venv or pin PyPortfolioOpt
to a build matching installed scipy, and report which combination actually runs.

`ANSWER 6:` ______

## 7. Phase-4 paper loop starter [READY after §2] — the calendar commitment begins

One evening:

```bash
.venv/bin/python -m indtrader.cli scan
.venv/bin/python -m indtrader.cli notify
```

Then the acceptance rule from the build plan: **20 trading days of ETL with zero gaps
vs bhavcopy**, and every paper fill entered with `fill SYMBOL BUY|SELL QTY PRICE
--costs <all-in>` within a day. No code needed — just don't skip evenings. 📅

`ANSWER 7:` first run date + any errors → ______

---

## Results table (I'll fill from your answers, you can too)

| # | Item | Status (PASS/FAIL/SKIP) | Notes |
|---|---|---|---|
| 0 | offline test suite | | |
| 1 | network reachability | | |
| 2 | live fetch + backtest + universe | | |
| 3 | bhavcopy contract test | | |
| 4 | yfinance Adj Close shape | | |
| 5 | NSE calendar/scrip endpoints | | |
| 6 | pypfopt import | | |
| 7 | paper loop started | | |
