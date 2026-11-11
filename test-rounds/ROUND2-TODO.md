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
$ .venv/bin/python -m indtrader.cli fetch NIFTY
[etl] NIFTY: 2918 rows stored

$ .venv/bin/python -m indtrader.cli fetch-universe --file data/universe.txt
(running in background via yfinance; ~33 distinct symbols / ~74k rows so far of 500; ~15s per symbol — partial is usable)

$ .venv/bin/python -m indtrader.cli fetch-adj RELIANCE
adj factors stored for 1/1 symbols

$ .venv/bin/python -m indtrader.cli backtest RELIANCE --strategy trend_sma200 --adjusted --costs
{
 "start": "2015-01-01",
 "end": "2026-11-09",
 "years": 11.85,
 "trades": 132,
 "total_return": -0.0715,
 "cagr": -0.0062,
 "sharpe": -0.24,
 "max_drawdown": -0.1311,
 "win_rate": 0.242,
 "profit_factor": 0.8,
 "avg_win": 9194.7,
 "avg_loss": -3656.89,
 "avg_days": 23.5,
 "total_costs": 29144.51,
 "buy_hold_cagr": null,
 "buy_hold_total": 6.0028
}

$ .venv/bin/python -m indtrader.cli health --file data/universe.txt
[health] 472 problem symbol(s) of 500 (thresholds: missing>2 sessions, stale>3)
(expected — only ~33 of 500 symbols backfilled yet)
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
2026-11-10: ERROR NSEFileUnavailableError: NSE file is unavailable or not yet updated.
method ok | returned: PosixPath | date: 2026-11-09
file: /private/var/folders/2x/g91s8qcs4nb6tzd19jm11z080000gn/T/nseapi_8epxj2ar/BhavCopy_NSE_CM_0_0_0_20261109_F_0000.csv | shape: (3704, 34)
columns: ['TradDt', 'BizDt', 'Sgmt', 'Src', 'FinInstrmTp', 'FinInstrmId', 'ISIN', 'TckrSymb', 'SctySrs', 'XpryDt', 'FininstrmActlXpryDt', 'StrkPric', 'OptnTp', 'FinInstrmNm', 'OpnPric', 'HghPric', 'LwPric', 'ClsPric', 'LastPric', 'PrvsClsgPric', 'UndrlygPric', 'SttlmPric', 'OpnIntrst', 'ChngInOpnIntrst', 'TtlTradgVol', 'TtlTrfVal', 'TtlNbOfTxsExctd', 'SsnId', 'NewBrdLotQty', 'Rmks', 'Rsvd1', 'Rsvd2', 'Rsvd3', 'Rsvd4']
mapped -> symbol: None | close: None | class: None
column contract unknown — paste the columns line above; do not let me guess it

Note: NSE CSV uses TckrSymb / ClsPric / SctySrs (not SYMBOL / CLOSE / SECURITIES_CLASS). The probe's mapping keys need to match these spellings. Cross-check of known symbols against store:
RELIANCE bhav=1324.4 store=1324.4 last=2026-11-09
HDFCBANK bhav=798.15 store=798.15 last=2026-11-09
ICICIBANK bhav=1589.0 store=1589.0 last=2026-11-09
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
lib: nse.NSE NSE | methods: 69
['FNO_BANK', 'FNO_FINNIFTY', 'FNO_IT', 'FNO_NIFTY', 'HOLIDAY_CLEARING', 'HOLIDAY_TRADING', 'SEGMENT_DEBT', 'SEGMENT_EQUITY', 'SEGMENT_MF', 'SEGMENT_SME', 'UDIFF_SWITCH_DATE', 'actions', 'advance_decline', 'announcements', 'annual_reports', 'archive_url', 'base_url', 'block_deals', 'board_meetings', 'bulk_deals', 'circulars', 'cm_mii_security_report', 'compile_option_chain', 'delivery_bhavcopy', 'dir', 'download_document', 'equity_bhavcopy', 'equity_meta_info', 'equity_quote', 'exit', 'fetch_daily_reports_file_metadata', 'fetch_equity_historical_data', 'fetch_fno_underlying', 'fetch_historical_fno_data', 'fetch_historical_index_data', 'fetch_historical_vix_data', 'fetch_index_names', 'financial_results', 'fno_bhavcopy', 'fno_lots', 'gainers', 'get_detailed_scrip_data', 'get_futures_expiry', 'holidays', 'indices_bhavcopy', 'list_current_ipo', 'list_equity_stocks_by_index', 'list_etf', 'list_indices', 'list_past_ipo', 'list_sgb', 'list_sme', 'list_upcoming_ipo', 'live_volume_gainers', 'lookup', 'losers', 'max_pain', 'next_api_base_url', 'next_api_global_search_url', 'next_api_quote_url', 'opt_cache_dir', 'option_chain', 'pr_bhavcopy', 'press_releases', 'priceband_report', 'quote', 'results_comparison', 'shareholding', 'status']
--- HOLIDAY_CLEARING needs args: 'str' object is not callable
--- HOLIDAY_TRADING needs args: 'str' object is not callable
--- announcements () -> [{'an_dt': '10-Nov-2026 23:55:09', 'attFileSize': '204.81 KB', 'attchmntFile': 'https://nsearchives.nseindia.com/corporate/CGCL_10112026235043_SEIntimation_Investormeetings.pdf', 'attchmntText': 'Capri Global Capital Limited has informed the Exchange about Schedule of meet', 'bflag': None, 'csvName': None, 'desc': 'Analysts/Institutional Investor Meet/Con. Call Updates', 'difference': '00:00:01', 
--- get_detailed_scrip_data ('RELIANCE',) -> {'equityResponse': [{'orderBook': {'buyPrice1': 0, 'buyQuantity1': 0, 'buyPrice2': 0, 'buyQuantity2': 0, 'buyPrice3': 0, 'buyQuantity3': 0, 'buyPrice4': 0, 'buyQuantity4': 0, 'buyPrice5': 0, 'buyQuantity5': 0, 'sellPrice1': 0, 'sellQuantity1': 0, 'sellPrice2': 0, 'sellQuantity2': 0, 'sellPrice3': 0, 'sellQuantity3': 0, 'sellPrice4': 0, 'sellQuantity4': 0, 'sellPrice5': 0, 'sellQuantity5': 0, 'last
--- holidays () -> {'CBM': [{'tradingDate': '15-Jan-2026', 'weekDay': 'Thursday', 'description': 'Municipal Corporation Election - Maharashtra', 'morning_session': None, 'evening_session': None, 'Sr_no': 1}, {'tradingDate': '26-Jan-2026', 'weekDay': 'Monday', 'description': 'Republic Day', 'morning_session': None, 'evening_session': None, 'Sr_no': 2}, {'tradingDate': '15-Feb-2026', 'weekDay': 'Sunday', 'description'
```

>>> PASTE 3b:
```
--- archive_url no signature: 'https://nsearchives.nseindia.com' is not a callable object
--- fetch_equity_historical_data (symbol: str, from_date: Optional[datetime.date] = None, to_date: Optional[datetime.date] = None, series: Literal['ae', 'af', 'be', 'bl', 'eq', 'il', 'rl', 'w3', 'gb', 'gs'] = 'eq') -> List[Dict]
--- fetch_historical_fno_data (symbol: str, instrument: Literal['futidx', 'futstk', 'optidx', 'optstk', 'futivx'] = 'futidx', from_date: Optional[datetime.date] = None, to_date: Optional[datetime.date] = None, expiry: Optional[datetime.date] = None, option_type: Optional[Literal['ce', 'pe']] = None, strike_price: Optional[float] = None) -> List[dict]
--- fetch_historical_index_data (index: str, from_date: Optional[datetime.date] = None, to_date: Optional[datetime.date] = None) -> List[Dict]
--- fetch_historical_vix_data (from_date: Optional[datetime.date] = None, to_date: Optional[datetime.date] = None) -> List[Dict]
OK  date objects -> list 10
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
HTTP Error 404: {"quoteSummary":{"result":null,"error":{"code":"Not Found","description":"Quote not found for symbol: JIO.NS"}}}
$JIO.NS: possibly delisted; no timezone found
SBIN last split: 2014-11-20 ratio 10.0
HDFCBANK last split: 2025-08-26 ratio 2.0
ICICIBANK last split: 2017-06-20 ratio 1.1
ITC last split: 2016-07-01 ratio 1.5
TATASTEEL last split: 2022-07-28 ratio 10.0
LT last split: 2017-07-13 ratio 1.5
BHARTIARTL last split: 2009-07-24 ratio 2.0
ADANIPORTS last split: 2010-09-23 ratio 5.0
TITAN last split: 2011-06-23 ratio 10.0
ASIANPAINT last split: 2013-07-30 ratio 10.0
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
scipy 1.18.1
pypfopt 1.6.0
import OK
Traceback (most recent call last):
  File "<string>", line 5, in <module>
    print(HRPOpt(r).optimize())
          ~~~~~~~~~~~~~~~~~~^^
  File ".../pypfopt/hierarchical_portfolio.py", line 152, in optimize
    if linkage_method not in sch._LINKAGE_METHODS:
                             ^^^^^^^^^^^^^^^^^^^^
AttributeError: module 'scipy.cluster.hierarchy' has no attribute '_LINKAGE_METHODS'
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
$ .venv/bin/python -m indtrader.cli scan 2>&1
HTTP Error 404: {"quoteSummary":{"result":null,"error":{"code":"Not Found","description":"Quote not found for symbol: NIFTY.NS"}}}
$NIFTY.NS: possibly delisted; no timezone found
insufficient data — run fetch-universe first (home network)
[cli] NIFTY not in store — fetching from provider...
[etl] NIFTY: 0 rows stored

$ .venv/bin/python -m indtrader.cli notify 2>&1
ind-trader 2026-11-11
Regime NEUTRAL | exposure 0.6 | breadth 0.75
Shortlist:
 - LAURUSLABS  2454.0  score 1  NA  mom6m +100%  deliv 36.7%
 - GLAND  3302.4  score 0.952381  NA  mom6m +73%  deliv 23%
 - DIVISLAB  11565.5  score 0.904762  NA  mom6m +72%  deliv 30.5%
 - SAILIFE  1836.8  score 0.857143  NA  mom6m +65%  deliv 14.3%
 - KALYANKJIL  611.0  score 0.809524  NA  mom6m +44%  deliv 33.4%
no data: SUNPHARMA, HCLTECH, IREDA, ADANIPORTS, INDHOTEL, TECHM, SHRIRAMFIN, GLENMARK
Regime NEUTRAL — breadth 75%; ON needs index not above its 200-DMA
[notify] not sent: telegram not configured
```

## 7. Sanity check (10 seconds)

```bash
.venv/bin/python -m pytest -q
```

>>> PASTE 7 (just the last line):
```
174 passed, 1 skipped, 1 warning in 1.71s
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
