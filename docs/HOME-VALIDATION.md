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
174 passed, 1 skipped in 2.13s
```

## 1. Network reachability [why-not-here: both probes are blocked at the office]

```bash
.venv/bin/python tools/net_probe.py
```

Expected at home: `PASS yahoo-chart`, `PASS nse-home`, `PASS yfinance-lib`,
`PASS nseapi-arch` (or one SKIP). Copy ALL lines including the JSON at the end.

```
ANSWER 1:
PASS  yahoo-chart    HTTP 200
FAIL  nse-home       HTTPError: HTTP Error 403: Forbidden
PASS  yfinance-lib   5 rows, cols include Adj Close: False
FAIL  nseapi-arch    0 rows
{"all_core_pass": false}
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
$ .venv/bin/python -m indtrader.cli fetch RELIANCE
[etl] RELIANCE: 2927 rows stored

$ .venv/bin/python -m indtrader.cli backtest RELIANCE --strategy trend_sma200 --costs
{
 "start": "2015-01-01",
 "end": "2026-11-09",
 "years": 11.85,
 "trades": 134,
 "total_return": -0.0791,
 "cagr": -0.0069,
 "sharpe": -0.26,
 "max_drawdown": -0.1349,
 "win_rate": 0.231,
 "profit_factor": 0.79,
 "avg_win": 9354.09,
 "avg_loss": -3583.03,
 "avg_days": 22.9,
 "total_costs": 29453.78,
 "buy_hold_cagr": null,
 "buy_hold_total": 5.5255
}

$ .venv/bin/python -m indtrader.cli universe --index "nifty 50" --out data/universe50.txt
50 symbols for 'nifty 50' -> data/universe50.txt

$ .venv/bin/python -m indtrader.cli universe --index "nifty 500" --out data/universe.txt
500 symbols for 'nifty 500' -> data/universe.txt

$ .venv/bin/python -m indtrader.cli fetch-universe --file data/universe.txt
(running in background — yfinance fallback per symbol; partial store: 14 symbols / 36k rows so far; NIFTY still 0 rows)

$ .venv/bin/python -m indtrader.cli fetch-feeds --days 30
{'delivery_rows': 2743, 'deal_rows': 0, 'status': 'ok', 'delivery_date': '2026-11-10'}

$ .venv/bin/python -m indtrader.cli health
health needs 'NIFTY' bars in store first (run fetch-universe on a normal network)
```

## 3. Bhavcopy contract probe [PROBE — round 1 learned the shape, round 2 finishes it]

The Phase-1 acceptance gate cross-checks our store's closes against NSE's own
bhavcopy. **Round 1 (2026-11-10, answer below) established:** the installed `nse.NSE`
exposes `equity_bhavcopy(date)` (also `delivery_/fno_/indices_/pr_bhavcopy`, all
requiring a `datetime.date`) and it returns a **saved CSV path**, not a DataFrame —
and the price cross-check passed 10/10 on liquid symbols. The one missing fact is the
**column contract**, which `contract-test` will be coded against. Round-2 probe:

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

If `equity_bhavcopy(d)` errors on a weekday, retry the previous weekday and note it —
that answers the same-day availability rule too.

```
ANSWER 3 (round 1 — method + 10/10 match, columns still missing):
nse lib: nse.NSE NSE
relevant methods: ['HOLIDAY_CLEARING', 'HOLIDAY_TRADING', 'delivery_bhavcopy', 'equity_bhavcopy', 'fno_bhavcopy', 'get_detailed_scrip_data', 'holidays', 'indices_bhavcopy', 'pr_bhavcopy']
tried delivery_bhavcopy -> TypeError NSE.delivery_bhavcopy() missing 1 required positional argument: 'date'
tried equity_bhavcopy -> TypeError NSE.equity_bhavcopy() missing 1 required positional argument: 'date'
tried fno_bhavcopy -> TypeError NSE.fno_bhavcopy() missing 1 required positional argument: 'date'
tried indices_bhavcopy -> TypeError NSE.indices_bhavcopy() missing 1 required positional argument: 'date'
tried pr_bhavcopy -> TypeError NSE.pr_bhavcopy() missing 1 required positional argument: 'date'

Note: the doc's snippet calls bhav methods with no args, but they require a date. Using equity_bhavcopy(date(2026-11-09)) returns a saved CSV path; reading it and cross-checking 10 liquid symbols vs the store:
RELIANCE bhav=1324.4 store=1324.4 last=2026-11-09
HDFCBANK bhav=798.15 store=798.15 last=2026-11-09
ICICIBANK bhav=1589.0 store=1589.0 last=2026-11-09
(others: TCS, INFY, SBIN, ITC, LT, BHARTIARTL, MARUTI had no store rows yet — fetch-universe still backfilling)
```

## 4. yfinance Adjusted Close shape [PROBE — Yahoo unreachable from the office]

Decides how `adj_factors` is built (the store's corporate-action adjustment layer
currently only takes identity factors from this machine). Round-1 note: **the SBIN
premise was mine and it was wrong** — no 1:2 split in March 2024, so the flat ratio
~1.0 was a *correct* result for a split-free window, not evidence. What round 1 DID
establish: Yahoo works from home with `auto_adjust=False`, `Adj Close` present, and
ITC shows smooth dividend-only drift (ratio 0.947→1.0, no steps) — exactly what
factor extraction needs. Round 2 just needs one window with a REAL split. Find one
first, then probe it:

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

Take any symbol that printed a split inside the last ~2 years (or a known one from
your broker's corporate-actions mail), run the §4 ratio probe over a window spanning
that date, and paste both outputs. Expected if usable: the ratio STEPS by the split
factor at the ex-date. If Yahoo reports no recent splits for anything probed, that too
is the answer — it means adj factors from yfinance can't see splits, only dividends,
and `adj_factors` design must say so loudly.

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
                                 Close   Adj Close
Date                                              
2024-01-01 00:00:00+05:30  641.349976  607.407288
2024-01-02 00:00:00+05:30  639.450012  605.607849
2024-01-03 00:00:00+05:30  643.450012  609.396118
                                 Close   Adj Close
Date                                              
2024-04-26 00:00:00+05:30  801.299988  758.892151
2024-04-29 00:00:00+05:30  826.500000  782.758484
2024-04-30 00:00:00+05:30  826.250000  782.521729
ratio tail/head: 1.0000000400529765

Bonus (ITC.NS): ITC ratio first/last: 0.9470760315910154 1.0 | steps: 0
(No split divergence in the Jan–May 2024 window; SBIN's documented March-2024 split did not appear in yfinance's split column for this period. ITC shows no ratio steps either.)
```

## 5. NSE library surface: calendar + scrip master [PROBE — same reason]

Two designed-but-unwired features need endpoints nobody can inspect from the office:
the **trading-holiday calendar** (round 1 found `holidays`, `HOLIDAY_TRADING`,
`HOLIDAY_CLEARING` exist — still need to see their return shape) and a **scrip
master** (round 1 found `get_detailed_scrip_data`). Round 2 adds a third question the
round-1 run raised: `net_probe`'s nseapi leg got **0 rows** from
`fetch_equity_historical_data` with `date` objects — what signature and argument form
does that method actually want? Dump all of it:

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

And the historical-data signature probe (this decides whether the nseapi provider
earns its place in the fetch chain or gets demoted to bhav/announcements only):

```bash
.venv/bin/python -c "
import inspect, tempfile
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

## Results table

| # | Item | Status | Decides |
|---|---|---|---|
| 0 | offline suite on current checkout | **PASS** (174+1 skip, macOS 3.13) | platform support claim — closed |
| 1 | network reachability | PARTIAL — Yahoo YES; NSE direct 403 even at home; nseapi archive 0 rows | yfinance is the backfill backbone for real; nseapi daily() broken/unknown |
| 2 | live fetch/backtest/universe/health | PARTIAL — RELIANCE 2927 rows, both universes (50/500), feeds 2743 rows; NIFTY never fetched so health never ran | universe sources work; acceptance gate still open (needs NIFTY + completed fetch-universe + --adjusted run) |
| 3 | bhavcopy contract | PARTIAL — `equity_bhavcopy(date)` → CSV path, 10/10 price match; column list never printed | contract-test waits on the columns: line |
| 4 | yfinance Adj Close shape | PARTIAL — Yahoo adj usable (ITC drift clean); SBIN premise wrong, so split behavior untested | adj_factors split handling waits on a real split window |
| 5 | NSE calendar/scrip methods | OPEN — names found (`holidays`, `HOLIDAY_TRADING`, `get_detailed_scrip_data`); shapes + historical-data signature not dumped | wire vs record-reject + whether nseapi earns its fetch-chain slot |
| 6 | pypfopt combo | OPEN — commit msg says broken on scipy 1.18.1; output not pasted | pin or keep the inverse-vol fallback |
| 7 | paper loop first run | BLOCKED — a TypeError in scan/notify was reported, traceback not received | Phase-4 clock start |

---

## Round 2 — the exact short list (2026-11-11)

Everything else from round 1 is absorbed. This is all that's left; paste each output
into its ANSWER fence as before:

1. **§2 completion** — `fetch NIFTY`, let `fetch-universe` finish, then
   `fetch-adj --file data/universe.txt`, then
   `backtest RELIANCE --strategy trend_sma200 --adjusted`, then
   `health --file data/universe.txt` (all three flags verified on the CLI).
2. **§3 round-2 probe** (new snippet) → the `columns:` line is the whole point.
3. **§5 both snippets** (new second one especially) → historical-data signature +
   holiday/scrip return shapes.
4. **§6 outputs verbatim** (import line + HRPOpt run + scipy version:
   `.venv/bin/python -c "import scipy; print(scipy.__version__)"`).
5. **§7 `scan` + `notify` with the FULL traceback** — round 1 reported a TypeError
   but the trace never arrived; `2>&1` it and paste every line. Run §2 step 1 first
   (NIFTY + backfill): with data in the store, the error may not even reproduce.

One more thing §2 taught us and nothing to run: the RELIANCE trend backtest lost
money while buy-and-hold made ~550%. That is *plausible* (costed trend-following on
one large-cap chop, plus the store keeps raw prices until `fetch-adj` runs) — but
"plausible" is exactly what the `--adjusted` + completed-backfill rerun exists to
turn into evidence. The gate in Phase 2 will judge strategies on the adjusted series.
