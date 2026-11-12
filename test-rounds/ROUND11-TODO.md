# ROUND 11 — the operator had another AI review our design docs; its review
(`docs/ind-trader Design Review.docx`) made specific FACTUAL claims about live
data providers. The office box verified everything it could offline (see
CHANGELOG session notes — it did NOT change any code from the review). Six
claims can only be settled with real NSE/Yahoo data, i.e. on this laptop.
Run the probes, paste outputs verbatim. A claim being "probably true" is worth
nothing here — each probe exists because the FIX DESIGN depends on the answer.

Do ROUND8/9/10 leftovers first if open — this round does not replace them.
Probes 2, 4, 5, 6 are network-only (no code, no writes to our DB). Probe 1
writes factor rows for named symbols via the existing `fetch-adj` — that is
normal nightly behavior, safe.

Pull first: `git pull --ff-only`.

## 1. Yahoo Close vs exchange Close on a KNOWN split (review claim #2)

Claim: "Yahoo's Close column is already split-adjusted, so storing yfinance
candles next to NSE-raw ones double-adjusts." Our code counter-evidence: the
yf provider calls `auto_adjust=False` and HDFCBANK's yf factor series showed
NO split step (dividend-only) — which is the OPPOSITE signature. Settle it.

Pick one symbol from our OWN split ledger (it has exchange-verified dates —
HDFCBANK 2025-08-26 bonus 1:1, ratio 2.0, the very event of the ROUND-4 lie,
should be there):
```
sqlite3 data/bars.sqlite "SELECT symbol, d, ratio FROM split_factors ORDER BY d DESC LIMIT 10"
```
(if `split_factors` is empty, paste that too and say so — then pick any ratio
you can verify on the NSE corporate-actions page.) Then run this from the
repo root (it READS only — no store, no writes):

```
.venv/bin/python - <<'EOF'
import pandas as pd, yfinance as yf
sym = "HDFCBANK"          # <- replace with the symbol you picked
ex  = "2025-08-26"        # <- replace with its real ex-date (YYYY-MM-DD)
start = (pd.Timestamp(ex) - pd.Timedelta(days=12)).date().isoformat()
end   = (pd.Timestamp(ex) + pd.Timedelta(days=5)).date().isoformat()
h = yf.Ticker(sym + ".NS").history(start=start, end=end, auto_adjust=False)
h.index = pd.to_datetime(h.index, utc=True, format="mixed").tz_localize(None)  # same normalization as our provider
print(h[["Close", "Adj Close"]].round(2))
EOF
```

And the same window from our store:
```
.venv/bin/python - <<'EOF'
from indtrader.data.store import BarStore
import pandas as pd
s = BarStore(); sym = "HDFCBANK"; ex = "2025-08-26"   # <- same values as probe 1
df = s.load(sym)
w = df.loc[str(pd.Timestamp(ex) - pd.Timedelta(days=12)):str(pd.Timestamp(ex) + pd.Timedelta(days=5)), ["close"]]
print(w.round(2))
EOF
```

PASTE 1 (both tables + the symbol/ex-date pair chosen):
```
(both outputs)
```
Answer these explicitly:
- a) Around the ex-date, does OUR store show the cliff and does YAHOO `Close`
  ALSO show the SAME cliff? YES+YES → Yahoo Close is raw → review claim #2 is
  REFUTED for this provider config. YES+NO (yf smooth) → claim CONFIRMED →
  mixing providers really would double-adjust.
- b) Does yf `Adj Close / Close` show a factor STEP on the ex-date (divide the
  two columns day-by-day, look at the ratio before vs after)? NO step = yf
  missed the split = our recorded ratio is the only source (HDFCBANK
  pattern). This is the exact input the factor-confirmation fix needs.

## 2. NSE prev-close on an ex-date — the free CA detector (review claim #79)

Claim: NSE's own `prev_close` field is corporate-action-adjusted on ex-dates,
which would give an exchange-authored factor for EVERY action (1:2 bonuses
included) — closing the split-band gap for free. Needs one real ex-date
morning: pick a name from the live NSE announcements (record date past,
ex-date = TODAY or tomorrow) — corporate-actions page on nseindia.com.

```
curl -s "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050" -H "User-Agent: Mozilla/5.0" -H "Accept: application/json" | head -c 4000
```
(find any stock whose `previousClose` differs from yesterday's actual close —
an ex-date day makes this easy; OR run on the specific symbol page:
`https://www.nseindia.com/api/stock-equities-complete?symbol=SYM`
if that route still serves. If NSE blocks the calls, say exactly how it failed —
that IS a result: then the detector idea dies on reachability.)

PASTE 2:
```
a) which name, its ex-date, NSE's previousClose for today
b) our stored close for the PREVIOUS session:
   sqlite3 data/bars.sqlite "SELECT d, close FROM candles WHERE symbol='SYM' AND tf='daily' ORDER BY d DESC LIMIT 3"
c) VERDICT: NSE prev-close == our raw close (NOT adjusted — idea dead)
   or == the CA-adjusted figure (adjusted — free detector confirmed)
```

## 3. Factor step location window (review claim #3)

Claim: yfinance puts a factor step on ex-date ±1, and our confirmation test
looks at one day only. This probe MEASURES where the steps actually land.
The two tables have DIFFERENT jobs and the fix will compare them directly:
`adj_factors.factor` is Yahoo's own AdjClose/Close ratio (dividends + whatever
splits Yahoo happened to record); `split_factors.ratio` is OUR exchange-record
list. For each symbol with recorded actions (up to 6):

```
indtrader fetch-adj SYMBOL --start 2024-01-01
sqlite3 data/bars.sqlite "SELECT d, factor FROM adj_factors WHERE symbol='HDFCBANK' AND d BETWEEN '2025-08-20' AND '2025-08-30' ORDER BY d"
```
(replace symbol + the ±4-day window around each recorded ex-date from
`split_factors`; paste `.schema adj_factors` once too.)

PASTE 3:
```
schema line + one row-block per recorded action: does adj_factors.factor step
ON the ex-date, on ex-date±1, or not at all? (Compare against that symbol's
split_factors.ratio row.)
```

## 4. Evidence-feed publish times (review claim #43)

Our 16:05 loop fetches delivery% + bulk deals BEFORE NSE publishes them, so we
probably store yesterday's evidence while labeling it today's. One evening,
three rounds — each round = BOTH boxes, label them with the clock time:

```
sqlite3 data/bars.sqlite "SELECT max(d) FROM delivery_pct; SELECT max(d) FROM deals;"
indtrader fetch-feeds --days 2
```

(run round A at ~16:05 right after the loop fires, round B at ~17:45, round C
at ~19:45; `max(d)` = the newest session stored, and the printed result dict
shows what each feed reported that minute)

PASTE 4:
```
a) the three max(d) pairs with their clock times
b) the three fetch-feeds dicts
The publish time of each feed is the first round where max(d) jumps to TODAY.
If it never jumps tonight, say so with the outputs — that is also an answer.
```

## 5. Bhavcopy reachability + fields (review claim #78)

```
curl -sL "https://archives.nseindia.com/content/historical/EQUITIES/EQNI12NOV2026.zip" -o /tmp/eq.zip -H "User-Agent: Mozilla/5.0" ; file /tmp/eq.zip   # pick ANY past weekday
curl -sL "https://archives.nseindia.com/content/CM20112026_end.zip" -o /tmp/cm.zip -H "User-Agent: Mozilla/5.0" ; file /tmp/cm.zip             # sec_bhavdata_full, a past weekday
```

PASTE 5: `file` outputs + first 2 CSV lines after unzipping each + an
inventory: do the columns include trade count and delivery quantity? (That's
the review's claim that bhavcopy gives true traded value and delivery% in one
file per day — it decides whether we switch the primary ETL source.)

## 6. Momentum-index benchmark availability (review claim #64)

Can we pull Nifty200 Momentum 30 (TRI and price) history to benchmark the
scanner against — e.g. `^NSEMOM30` or another symbol on yfinance? Try both
symbol spellings; paste the first rows or the failure.

```
.venv/bin/python - <<'EOF'
import yfinance as yf
for s in ["^NSEMOM30", "NIFTYPHARM.NS"]:
    h = yf.Ticker(s).history(period="6mo")
    print(s, len(h)); print(h.tail(2))
EOF
```
(replace the second candidate with ^NSEI so the call always prints something
readable.)

PASTE 6:
```
(output)
```

## 7. Still open from ROUND10 (do not skip silently)

- §1 `coractions --resolve` paste, §2 gate smoke, §3 paper-loop chain,
  §4 dashboard live look + HDFCBANK `split_factors` rows, §5 holdings/watchers
  live validation, ROUND9 `[etl-loop]` day pastes.

Done = push this file back with pastes inline. "Didn't run" is a valid answer,
a fake paste is not.
