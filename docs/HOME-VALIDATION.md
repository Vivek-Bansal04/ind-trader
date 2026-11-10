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

---

## 0. Setup (one time)

```powershell
git clone https://github.com/Vivek-Bansal04/ind-trader.git
cd ind-trader
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev,nse]"
.venv\Scripts\python.exe -m pytest
```

Expected: all tests pass (≥106), fully offline. SAC note: use `python -m pip`, never
`pip.exe` (the shim is blocked here; the venv python is not).

`ANSWER 0:` pytest last line → ______

## 1. Network reachability [READY]

```powershell
.venv\Scripts\python.exe tools\net_probe.py
```

Expected at home: `PASS yahoo-chart`, `PASS nse-home`, `PASS yfinance-lib`,
`PASS nseapi-arch` (or one SKIP). Copy all lines + the JSON.

`ANSWER 1:` ______

## 2. Real data end-to-end [READY] — needs ANSWER 1 mostly PASS

Small smoke first (seconds), then the Phase-1 real thing (minutes, run overnight if slow):

```powershell
.venv\Scripts\python.exe -m indtrader.cli fetch RELIANCE
.venv\Scripts\python.exe -m indtrader.cli backtest RELIANCE --strategy trend_sma200 --costs

# universe from NSE itself (this needs the network):
.venv\Scripts\python.exe -m indtrader.cli universe --index "nifty 50"  --out data/universe50.txt
.venv\Scripts\python.exe -m indtrader.cli universe --index "nifty 500" --out data/universe.txt   # if this errors, note the error — Nifty-500 source is exactly what we need to learn
# 10-year backfill + evidence feeds:
.venv\Scripts\python.exe -m indtrader.cli fetch-universe --file data/universe.txt
.venv\Scripts\python.exe -m indtrader.cli fetch-feeds --days 30
```

Paste the `backtest` summary block and any errors verbatim (failures are data too).

`ANSWER 2:` ______

## 3. Bhavcopy contract test [PROBE] — will become `indtrader contract-test`

Cross-checks our store's last close for sample symbols against NSE's own bhavcopy —
the Phase-1 acceptance gate from the build plan. Run after §2 and paste the whole output:

```powershell
.venv\Scripts\python.exe -c "
import pandas as pd
from indtrader.data.store import BarStore
from indtrader.data.nseapi_provider import NseIndiaApiProvider
n = NseIndiaApiProvider()._nse()
df = n.daily_market_bhavcopy(date='today') if hasattr(n,'daily_market_bhavcopy') else None
print('bhavcopy method present:', df is not None, None if df is None else df.shape)
if df is not None:
    s = BarStore(); samp = df[df['SECURITIES_CLASS']=='E'].sample(5, random_state=1)
    for _,r in samp.iterrows():
        c = s.load(r['SYMBOL']).close
        ours = float(c.iloc[-1]) if len(c) else None
        print(r['SYMBOL'], 'bhav', r['CLOSE'], 'store', ours)
"
```

If the bhavcopy line errors, just paste the method list instead:
`.venv\Scripts\python.exe -c "from NseIndiaApi import NseIndia; print([m for m in dir(NseIndia) if 'bhav' in m.lower() or 'margin' in m.lower() or 'holiday' in m.lower() or 'scrip' in m.lower()])"`

`ANSWER 3:` ______

## 4. yfinance Adjusted Close shape [PROBE] — feeds the corporate-action factor design

```powershell
.venv\Scripts\python.exe -c "
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
ISIN/scrip master). Dump what NseIndiaApi actually offers + one sample:

```powershell
.venv\Scripts\python.exe -c "
from NseIndiaApi import NseIndia
n = NseIndia()
ms = [m for m in dir(n) if not m.startswith('_')]
print('methods:', ms)
for m in ms:
    if any(k in m.lower() for k in ('holiday','trading','scrip','master','corporate')):
        try:
            r = getattr(n, m)()
            print('---', m, '->', str(r)[:400])
        except TypeError as e:
            print('---', m, 'needs args:', e)
"
```

`ANSWER 5:` ______

## 6. pypfopt on this stack [READY] — FOUND BROKEN on the office box (2026-11-10)

`pypfopt 1.6.0` + current scipy: `HRPOpt.optimize()` dies on
`scipy.cluster.hierarchy has no attribute '_LINKAGE_METHODS'`. The code now catches
this and falls back to inverse_vol with a loud message, so nothing is blocked — but
if HRP is actually wanted, test whether an older combo works on the laptop:

```powershell
.venv\Scripts\python.exe -c "from pypfopt import HRPOpt; from pypfopt.risk_models import risk_matrix; print('pypfopt import OK')"
.venv\Scripts\python.exe -c "
import numpy as np, pandas as pd
from pypfopt import HRPOpt
r = pd.DataFrame(np.random.default_rng(1).normal(0, .02, (300, 4)), columns=list('ABCD'))
print(HRPOpt(r).optimize())"   # if this throws _LINKAGE_METHODS, HRP is dead on this scipy
```
If dead: try `python -m pip install 'scipy<1.15'` in a scratch venv or pin PyPortfolioOpt
to a build matching installed scipy, and report which combination actually runs.

`ANSWER 6:` ______

## 7. Phase-4 paper loop starter [READY after §2] — the calendar commitment begins

One evening:

```powershell
.venv\Scripts\python.exe -m indtrader.cli scan
.venv\Scripts\python.exe -m indtrader.cli notify
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
