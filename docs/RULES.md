# ind-trader — the complete rules reference

Every rule, threshold, refusal and retry the code actually enforces, read from
the source line by line on 2026-11-12 (no assumptions, no memory — all 64 files
under `src/indtrader/` plus `tools/` were read for this document). Each rule
names its file, quotes its constant exactly, and gets an example with real
numbers. Where a rule exists because of a live incident, the incident is named.

If this doc and the code ever disagree, the code wins — and the disagreement is
a bug worth reporting.

---

## 0. The constitution (rules that override everything else)

**0.1 Daily bars only, long-only, delivery/CNC.** No code path reads sub-daily
data; `backtest/strategies.py` — *"nothing here can look at sub-daily data"*.
Signal convention is 1 = long, 0 = flat. There is no SHORT anywhere.

**0.2 No order path exists.** Enforced by absence + tests:
`broker/fyers.py` has only `holdings()` — no order endpoint exists in the
package; `alerts/commands.py`'s `COMMANDS` dict is the exhaustive Telegram
allowlist (status/book/scan/events/ann/news/plan/plans/fill/pause/resume) and a
test asserts no order-verb ever appears among them; `models.OrderPlan`
deliberately has *no product/timeframe field to flip to intraday or margin*.
Example: `/fill REL BUY 100 2400` on the bot records a human decision ALREADY
made at the broker. It cannot place, modify or cancel anything.

**0.3 Unknown is never zero, absent is never calm.** The same sentence appears
across `news.py`, `macrocal.py`, `events.py`, `coractions.py`, `mf.py`,
`feeds.py`. Concrete forms:
- `data/feeds.py::net_bulk_value` returns `None` when deals were never
  ingested — *"unknown is different from zero!"* — vs `0.0` when deals exist
  and net to zero.
- `macrocal.py` keeps THREE states: True trading / False holiday / **None
  unknown** — *"absence from a holiday LIST proves nothing"*.
- `news.fetch_news` returns `ok=False` only when EVERY query failed, and the
  loop prints "news view is UNKNOWN not quiet" — a dead feed never renders as
  a quiet market (`run_daily.py`).

**0.4 Trading mode fails closed.** `config.py::execution_mode` —
*"paper | live ; anything else fails closed to paper"*:
`"live" -> live`, everything else (typo, empty, "LIVE " with space) → `paper`.

**0.5 Money rules are computation, not advice.** Every money output
(`tax.py`, `harvest.py`, `mf.py`, `rebalance.py`) carries *"computation, not
advice"* / *"verify with your CA"*; `rebalance.py` prints
*"proposals only — human-confirmed execution is the standing rule"*.

**0.6 Embedded market constants must carry an as-of date and age out.**
`tools/check_freshness.py`: every constant named `X_AS_OF = "YYYY-MM-DD"` is
scanned; a marker older than `--max-age-months` (**default 10** ≈ one Finance
Act cycle; month = 30 days) exits 1. No markers at all is itself a failure.
Example: `backtest/costs.py::RATES_AS_OF = "2026-11-01"`,
`portfolio/tax.py::TAX_SLABS_AS_OF = "2026-11-01"`.

---

## 1. Data fetching & the store (`data/`, `scheduler/`)

### 1.1 Provider chain
`etl.py::build_providers` — order from `DATA_PROVIDER_ORDER`, default
**`"nseapi,yfinance"`**. An unavailable provider is skipped *quietly but
logged* (`NseIndiaApi not installed — skipping`); an unknown name is logged and
skipped; if nothing remains, yfinance alone. `_fetch_one` tries providers in
order and takes the first non-empty answer; each empty answer costs a
`pause_s=0.3 s` sleep before the next provider.

**Why the full 2015→today range on every fetch** (`fetch_universe(start=
"2015-01-01")`): the provider call is ONE HTTP request for the whole range,
and full history lets each fetch repair middle holes and stale adjustment
factors, not just append the tail. Full story + examples in
[DATA-FETCHING.md](DATA-FETCHING.md).

### 1.2 Candle validity — enforced on write AND on read
`models.py::is_valid_candle`: no NaN; `open/high/low/close > 0`,
`volume ≥ 0`, `high ≥ max(open, close)`, `low ≤ min(open, close)`.
- `store.upsert_candles` **drops invalid rows silently at write** (count is
  what's reported), reads re-validate too (`load(validate=True)` prints
  `[data-quality] SYMBOL: dropped N invalid rows on read` — *"reading our own
  data is not automatically safe"*).
- `base.py::clean_daily` additionally drops duplicate dates (keeps last), and
  PRINTS loudly: any one-day close move **> 35%**
  (`jumps > 0.35` → "verify corporate action manually"), and any calendar gap
  **> max_gap_warn(=3) × 4 days** (~ a week+ → "possible missing sessions").
  Example: a raw HDFCBANK −50% day prints the warning — that's a split until
  proven otherwise, not an error to discard.

### 1.3 Upserts are idempotent
`store.upsert_candles` = `INSERT OR REPLACE INTO candles` keyed
(symbol, tf, d). Re-running a fetch can't duplicate a date; every write also
appends an `ingest_runs` audit row (symbol, tf, run_at, rows, source).

### 1.4 Freshness skip (the nightly loop's resume rule)
`etl.py::_is_complete_and_fresh` — a symbol is skipped (result `None`) iff
both hold:

1. **Calendar-anchored tail**: its last bar sits ON
   `expected = min(latest stored NIFTY daily bar, settled_cutoff)`, where
   `settled_cutoff` = **today before 16:15 IST means yesterday** (a
   today-stamped bar is an intraday snapshot — a 14:14 dry run once froze
   mid-session closes as final, round-6 live). A calendar-confirmed hole of
   ANY size refetches. *Bootstrap exception*: if NIFTY itself has no stored
   bars, the legacy lenient rule governs (busday gap ≤ `fresh_bdays=3` and no
   unsettled today-stamp).
2. **Depth**: first bar ≤ `start+45 days` (so history reaches ~2015), **or** a
   genuine new listing: first bar after start AND `n ≥ NEW_LISTING_MIN_ROWS=60`
   (~3 months of dailies — shallow-but-current stays suspect, because "fresh
   tail" alone would declare a shallow series done; round-5 live found ~193
   of 499 post-2015 listings failing the *old* both-prongs rule nightly,
   re-downloading pointlessly for 2+ minutes).

Example (your exact scenario): laptop off Monday+Tuesday, opened Wednesday.
NIFTY bars prove Tuesday was a session; every symbol's tail is Friday → hole →
all refetch Wednesday evening, both days land in one pass. Before the round-8
fix the gap was called "fresh" until it hit 4 business days.

Result semantics (`run_daily.run_once`): **`None` = skipped-fresh, `0` =
fetched-but-empty (loud, names symbols), `>0` = rows stored** — collapsing
None/0 once produced the false "EMPTY: all 499" alarm.

### 1.5 The ETL loop itself (`scheduler/run_daily.py`)
- Fires when `weekday() < 5` AND time ≥ **16:05 IST** AND hour < **23** — or
  immediately on boot (`run_now=True`). After a successful run it sleeps out
  the day in 10-min ticks; a FAILED run retries each 60 s inside the window —
  *"never die; the loop is the watchdog"*.
- After candles, best-effort: delivery/bulk-deal feeds, then news, then the
  evening briefing — each in its own try/except, each "non-fatal".

### 1.6 Parsers only accept CONFIRMED shapes (probe, never guess)
Applied at every external edge; a changed shape must raise naming what was
tried AND what was seen:
- `nseapi_provider.py` accepts BOTH camelCase (`chClosingPrice`) and legacy
  (`CH_CLOSING_PRICE`) keys (NSE moved them on 2026-11-11). If rows arrive but
  ZERO parse, it raises with the first row's actual keys — returning empty
  would fake "no such data" (the round-1 "0 rows" mystery).
- `macrocal.fetch_calendar` refuses unless `data.CBM` is a list of rows with
  `tradingDate` — shape drift → error string, stored rows keep serving.
- `coractions.parse_events`: `date`/`kind` key spellings must hit
  `CA_KEY_CANDS` or it raises listing both lists; optional keys absent is
  NORMAL (the confirmed payload has no ratio column).
- `shareholding.parse_pattern_rows`: every key in `{date, pr_and_prgrp,
  public_val}` must exist or raise; rows for OTHER symbols are dropped
  (payloads carry peers); placeholder values ("-", null) are skipped, never
  zeroed.
- `mf.parse_nav_all`: the AMFI header line must EQUAL the live-verified
  `HEADER` string after space-stripping, else refuse; zero parseable rows =
  *"treat as outage, not as empty"*.
- `contract.bhav_close_map`: needs `TckrSymb`/`ClsPric` (the probed names) or
  refuses — empty dict would silently pass every cross-check.

### 1.7 Probes try EVERY form and report every miss
`coractions.try_fetch` / `shareholding.try_fetch` sweep every method matching
`corporate|ca_|action` (resp. `shareholding|shp`) × every plausible arg form —
the unverified `option_type="equities"` claim is tried FIRST, TypeError is
REPORTED as `signature-miss`, endpoint errors `break` (don't hammer), every
non-empty payload is stored RAW before parsing. The unparseable payload's shape
report IS the deliverable.

### 1.8 Transport honesty (`data/http.py`)
`http_get` returns `(body, error)` — exactly one set; errors are strings,
never exceptions, so callers distinguish *unreachable* from *empty* without
try/except. `transport()` reports WHICH transport ran (`curl_cffi` Chrome
impersonation when installed — the only one proven through the laptop's WAF —
else `urllib`); a fetch result without its transport named is *"half a
result"*. `get_json`: malformed JSON is an error string, *"not a None that
could be mistaken for 'no data'"*. Timeouts: urllib/curl_cffi **15 s**, broker
**20 s**, Telegram **15 s**, AI providers **30 s**, Ollama availability probe
**1.5 s**.

### 1.9 NSE response-key drift reporting
`nseapi_provider.daily` prints `[nseapi SYM: dropped D/(D+N) rows on key
parse]` only when `dropped > parsed` (partial drift) — minor noise doesn't
mask, total failure raises (see §1.6).

### 1.10 Raw payload archiving
Corporate actions, shareholding and holiday rows are stored verbatim BEFORE
parsing (`corporate_actions_raw`, `shareholding_raw`,
`calendar_events.payload ≤ 900 chars`), so any parse decision is re-checkable
offline forever (`--from-raw` replays the parser over stored payloads and
BUILDS factors).

### 1.11 Backups (`scheduler/backup.py`)
sqlite3 online-backup API (safe while ETL writes); every backup gets
`PRAGMA integrity_check` — *"a backup that fails to open is a rumor, not a
backup"* — failed targets are deleted; retention keeps newest `keep=14` per
DB stem; restore refuses an integrity-failing backup and keeps the current file
as `<target>.pre-restore`.

---

## 2. Price adjustment & corporate actions (`data/store.py`, `research/`)

### 2.1 The merge never double-counts (`store.combined_factors`)
`f[t] = b[t] · Π{ r_D : D > t }` where `b` = yfinance factors re-anchored so
the NEWEST day = 1.0, and `r_D` = a recorded split's price multiplier on
ex-date D.
- **No-double-count test**: yf's own factor step on the ex-date,
  `step = b[D]/b[prev]`; if `|step · r − 1| ≤ 0.02` yf already priced it →
  the recorded ratio adds nothing (counted, printed: *"already priced by
  yfinance factors — not double-applied"*).
- A split's ratio applies to days **strictly before** D — the ex-date's own
  raw price is post-split.
- Days before the FIRST stored yf factor take that OLDEST factor level,
  **never an assumed 1.0** — 1.0 means "no adjustment" and would silently
  erase history (round-5 trap).
- Example: recorded bonus 1:1 (r=0.5) on a day where yf's step is 0.500 →
  `|0.5·0.5 − 1| = 0.75 > 0.02` → yf did NOT price it → 0.5 multiplies all
  days before the ex-date. If yf's step were 2.0 (the 1/r back-adjust step):
  `|2.0·0.5 − 1| = 0 ≤ 0.02` → skipped.

### 2.2 Ratio conventions (`coractions._ratio_from_num_text`) — the exchange's words or nothing
The ONLY sources: numbered statements inside the subject text. faceVal is kept
in its own field and NEVER used to derive a ratio.
- bonus `a:b` → `a/(a+b)` — **bonus 1:1 → 0.5**;
- plain ratio `a:b` with unknown notation → **the shrink**, `min/max`
  (equal → None: a 1:1 split with no bonus word is ambiguous, stays UNRESOLVED);
- `"Bonus 1"` (digit, no colon) → 0.5;
- face-value / from-to phrase: anchored regexes — only `Rs X … to … Rs Y`
  (≤ 3 words between) or `from X … to Y` (≤ 3 words), so **date pairs can
  never mint a ratio** ("from 01-Apr to 15-Apr" matches nothing because
  `01-Apr` isn't a bare number before `to`); equal amounts → None.
  Live spelling (round-7): *"Face Value Split (Sub-Division) - From Rs 2 Per
  Share To Rs 1 Per Share"* → 2→1 → **0.5**.
- no usable numbers → `None` → event stored with ratio NULL → **UNRESOLVED**,
  kept red by health/gap-list until a human confirms.

### 2.3 `--set` (human-confirmed event) refuses bad input
`coractions.record_manual_event("DATE|KIND|RATIO")`: ratio must parse, must be
in **[0.01, 100]** and `!= 1.0`; a typo'd ratio out of sane range is refused,
not stored. Only split-like kinds (`split|bonus|sub div|face val`,
`is_split_kind` regex) create a factor; a dividend recorded with a ratio is
event-only.

### 2.4 Split-gap gate (`research/splitgaps.py` — ONE implementation, 3 callers)
Any 1-day **raw** move with `|pct| ≥ SPLIT_JUMP = 0.35` must coincide with a
merged-factor step in **[0.4, 0.7] ∪ [1.4, 2.5]** on that day; otherwise the
symbol is named (with the factor step it DID have). A step of ~1.0 under a
−50% cliff is the exact HDFCBANK round-4 lie this catches. Printed by `health`
but NOT a gate; gates are §8.1. Example: 2025-08-26 −50% with factor step
0.499 → explained, quiet. Same move with step 0.998 → named:
`2025-08-26 -50% (factor step 0.998)`.

### 2.5 Dates NSE uses are parsed or fatal
`payloads.parse_iso_day` accepts ISO and `%d-%b-%Y` (plus `%d-%m-%Y`,
`%d/%m/%Y`); anything else RAISES — *"a split whose date cannot be read must
stop the parse, not shift the factor a few months"* (`build_split_factors`).
`quarter_label` maps quarter-end months {3→Q4, 6→Q1, 9→Q2, 12→Q3} of the
Indian FY (April start); parse failure raises, never stores a guessed label.

### 2.6 Calendar precedence (`macrocal`)
- A stored NIFTY **bar beats a holiday row** — *"what happened beats what was
  planned"*; both together = **disputed** (the drift ledger
  `disputed_dates()`), never resolved silently. Round-5 live: 5 disputed
  dates (2026-02-19, 03-19, 04-01, 08-26 holidays with 494–500 real bars).
- `iso_trading_day` is 3-state exactly as §0.3.
- The holiday master is the SAME source class as the nse lib's `holidays()` —
  stored as evidence, explicitly *"NOT an independent second opinion"*; the
  independent (BSE/RBI/SEBI) calendar stays on the backlog rather than fake
  agreement.

### 2.7 Contract test (`data/contract.py`)
Store closes vs NSE's OWN bhavcopy, symbol-matched (never instrument-type
guessed). Tolerance `--tolerance 0.01` ₹; PASS needs `checked ≥
--min-checked(5)` AND zero mismatches. Bhavcopy for session D exists only
after settlement, so the CLI walks back day-by-day (weekends skipped, ≤ 7
attempts, each skip printed) instead of dying on "today".

### 2.8 Holidays cross-check (`cli.cmd_holidays`) classifies by SHARE, not count
(494/500 = market-wide → calendar row suspect; ≥ 50% prints
`MARKET-WIDE` + the distinct-close count + the prior-trading-day-missing
off-by-one signature; ≥ 5% prints `ambiguous`; < 5% prints `yfinance feed
leakage`). Lesson baked in: round-4 mislabeled "494 symbols" as leakage
because the universe wasn't in the denominator.

---

## 3. Indicators (`ta.py`) — exact conventions

| Function | Exact definition (pandas calls verbatim) | Guard/example |
|---|---|---|
| `sma(s,n)` | `rolling(n, min_periods=n).mean()` | NaN until n bars — never a partial average |
| `ema(s,n)` | `ewm(span=n, adjust=False, min_periods=n).mean()` | NaN until n bars |
| `rsi(s,n=14)` | **Wilder's exact recursion**: seed = simple mean of gains/losses over first n; then `avg = (avg·(n−1) + x)/n` | flat market → **50** (`ag==0 and al==0 → 50.0`), never the fake-100 bug (stock-analyzer's Cutler RSI); `al==0 → 100`; NaN deltas propagate NaN |
| `atr(df,n=14)` | TR = max(H−L, \|H−C_prev\|, \|L−C_prev\|), then `ewm(alpha=1/n, adjust=False, min_periods=n)` (RMA) | NaN until n+1 bars |
| `macd` | EMA12−EMA26; signal = `ewm(span=9, adjust=False)` of the line | |
| `bollinger(20, 2)` | mid=SMA20, bands mid ± 2·`rolling.std(ddof=0)` | population std |
| `donchian(55)` | rolling max(high)/min(low), min_periods=period | no shift inside — callers shift |
| `annualized_vol(20)` | `pct_change().rolling(20, min_periods=20).std(ddof=0) · √252` | |
| `supertrend(10, 3)` | iterative ratcheting ATR bands around HL2; trend flips on close crossing the final band | |
| `resample_weekly` | `W-FRI` OHLCV (open first, high max, low min, close last, volume sum); **trailing partial week DROPPED** (`last_daily < wk and (wk − last_daily).days < 7 → drop`) | today's unfinished bar can never pollute weekly signals |

---

## 4. Regime (`research/regime.py`) — one traffic light, three states

`classify(breadth, index_above_200dma)`:

| Input | Label | exposure_scale |
|---|---|---|
| breadth **≥ 0.60 AND** index > SMA200 | ON | **1.0** |
| breadth **≤ 0.35 OR** index ≤ SMA200 | OFF | **0.30** |
| otherwise (in between) | NEUTRAL | **0.60** |
| **either input None** | NEUTRAL | **0.60** |

The None row is the rule: *missing data defaults cautious, never bold* —
unknown never buys a full position, and never forces a panic cut either.

- breadth = fraction of universe with `close > own SMA200`; a name counts only
  if its SMA200 is computable (`sma(closes,200).notna().sum() and
  closes.notna().sum()`) — `universe_n` records who counted.
- **flip_note** is the anti-vibes rule: it must print the exact numbers that
  would flip the light — OFF→ON: *"breadth to 60% (K more of N names above
  their 200-DMA)" and "index back above its 200-DMA (level)"*; ON→OFF:
  breadth under 35% (K of N must drop) **or** index close below; NEUTRAL
  prints what's missing, `unknown` printed as unknown, *"never rounded off to
  a comfortable number"*.
- **Follow-through (FTD): informational only, never touches exposure** (the
  dataclass comment is explicit: `ftd: ... never affects exposure_scale`).
  Constants: `FTD_CORRECTION_PCT = 0.05`, `FTD_MIN_DAY = 4`,
  `FTD_WATCH_SESSIONS = 25`, `FTD_LOOKBACK = 300`. Threshold is **σ-scaled**,
  `min(0.02, max(0.01, 1.5·σ))` — a fixed 1.5% almost never fires on NIFTY.
  State machine: uptrend → correction at −5% off peak → rally_attempt on any
  up day → FTD needs day ≥ 4 of the rally, change ≥ threshold, AND volume up
  (volume only checked when the full window has volume; missing volume →
  no FTD fires) → follow_through fails if a close drops below the rally low →
  reverts to uptrend if it holds 25 sessions or price reclaims the old peak.
  **< 60 bars → None** (*"absent data stays absent, it never defaults to a
  friendly state"*).
- Example: breadth 0.62, index above → ON ×1.0; the note prints
  "turns OFF if index close below its 200-DMA or breadth under 35% (135 of
  499 names must drop below their 200-DMA)".

---

## 5. Scanner (`research/scanner.py`) — gates score nothing, scores gate nothing that matters

Per-symbol evidence row, then a ranked table. Order matters:

1. **< 210 bars** → `tradable=False`, note `only N bars` — *"need a 200-DMA
   gate to say anything at all"*. Row still shows the close, scores 0.
2. **Trend gate (hard)**: `tradable = close > SMA200` (Faber). Below → not a
   swing long, full stop; quadrant/momentum still displayed for context.
3. **Liquidity floor (hard)**: mean(close·volume) over the 20 sessions ending
   at the signal ≥ `LIQUIDITY_FLOOR_TURNOVER = 2e7` (₹2 Cr/day). **Missing
   volume or < 10 joined rows → `(True, None)` — unknown does not
   disqualify**; present-and-below → `tradable=False` + note `illiquid
   (<2Cr/day)`. The window deliberately ends at the signal (no look-ahead).
4. **Quadrant score (soft)**: `{"Improving": 2.0, "Leading": 1.5, "Weakening":
   0.0, "Lagging": -1.0, "NA": 0.0}` — added ONLY when tradable.
5. **Momentum (soft)**: `+ percentile rank of mom_6m` (126-session
   `pct_change`) among tradable names.
6. **Net bulk buying (soft)**: `+0.5` iff net bulk ₹ value over the last 7
   days > 0 — *"only net positive bulk buying boosts (+0.5)"*; a NEGATIVE net
   bulk changes nothing. Delivery % **never boosts** — it only annotates.
7. Announce-only annotations (never score): delivery `dp < min(avg20, 40.0)` on
   a trend day → note `low delivery on trend`; `base_quality`: 20-session
   close-range/ATR14 ≤ **COMP_RANGE_ATR = 0.90** AND |close/EMA21 − 1| ≤
   **COMP_EMA_DRIFT = 0.03** → `compressed base` (annotation: *"a tight base
   ANNOTATES, never boosts — guessing a directional weight for it is the fudge
   we reject"*).
8. Sort `tradable` first, then score; `exposure_scale` from regime rides along.

Example row: TATA close 240, SMA200 210 ✓; turnover 20d ₹6.3 Cr ✓; quadrant
Improving (+2.0); mom6m +18% ranks 0.77 → score 2.77; netBulk +₹40 Cr → +0.5
→ **3.27**. A name with the same stats but 0.9 Cr/day turnover: `tradable=
False`, score forced to 0.

---

## 6. RRG (`research/rrg.py`) — clean-room (the audited repo had no license)

- `RS = stock/bench` on inner-joined dates; `RS-Ratio = 100·EMA₁₄(RS) /
  MA₁₄(EMA₁₄(RS))`; `RS-Momentum = 100 + 100·EMA₁₄(ROC₁₀(RS-Ratio))`.
- Quadrants: RSR≥100,RSM≥100 → Leading; ≥100,<100 → Weakening; <100,<100 →
  Lagging; <100,≥100 → Improving; NaN → "NA".
- Need ≥ `max(m,k)+5 = 19` joined points or the symbol is skipped (never
  plotted from a stub).
- `transitions()` extracts Improving→Leading etc. — *"the actionable RRG
  signal"* — but note §11.5: the scanner today scores the CURRENT quadrant
  only.
- CLI `rrg` resamples to W-FRI last-value before the table (weekly is the
  natural period); scanner path stays daily (known simplification, ALGORITHMS
  §11.1).

---

## 7. Backtesting (`backtest/`)

### 7.1 Execution rules (single-symbol engine)
- **Signal on bar i fills at bar i+1's OPEN** — never the same bar's close
  (`engine.run` reads `s[i-1]` at `o[i]`); strategies never shift themselves.
- Slippage moves the FILL, not the cost line: buy at `open·(1+slip)`, sell at
  `price·(1−slip)`, `DEFAULT_SLIPPAGE = 0.0005` (5 bps/side).
- Stop entry condition: previous ATR must exist (`not np.isnan(a[i-1])`) —
  no ATR, no trade.
- Trailing stop ratchets UP only: `stop = max(stop, close − 3.0·ATR14)`
  (`stop_atr_mult=3.0`, `atr_period=14`); a gap through the stop fills at
  `min(stop, open)`.
- Exit priority on a bar: stop → `time_stop_days` (if set) → signal off
  (s==0, only for positions entered strictly before today). End of data
  liquidates at last close, reason `end_of_test`.
- Default sizing: `min(0.02·equity / (3·ATR), max_alloc(0.10)·equity/price)`
  — backtest risks **2%**, live sizing defaults to **1%** (§8.1): deliberate
  stress, flagged in ALGORITHMS §11.3.
- `buy_qty_for` rounds down share-by-share until `qty·price + buy_costs ≤
  cash`; lots of 1 share (delivery has no F&O lot sizes).

### 7.2 Indian delivery costs (`backtest/costs.py`, as of 2026-11-01)
STT 0.1% buy + 0.1% sell; exchange txn 0.00297%; SEBI 10/crore; stamp duty
0.015% buy; SLBM 0.003% buy (kept "conservatively" for delivery); GST 18% on
(brokerage + exchange txn + SEBI); brokerage 0 default (discount CNC), with
`brokerage_rate`/`brokerage_cap=200` knobs. Round-trip fraction helper
`round_trip_fraction(notional)`. Everything is
**conservative-high**; correct in ONE place when slabs change (rule 0.6).
Example: ₹1,00,000 buy ≈ STT 100 + stamp 15 + SLBM 3 + txn 2.97 + SEBI 0.10 +
GST 0.55 ≈ ₹121.6; same sell ≈ ₹103.6 → round trip ≈ **0.225%**.

### 7.3 Strategies (daily bars, long/flat; REGISTRY = the only six)
| name | rule | gate |
|---|---|---|
| `trend_sma200` | long while close > SMA200 | (is the gate) |
| `dual_ma` | EMA20 > EMA100 | + close > SMA200 |
| `donchian` | close > prior 55d high; exit close < 20d low (ffill state) | + close > SMA200 |
| `ts_mom` | 126d return > 0 | + close > SMA200 |
| `rsi_reg` | buy RSI2 < 10, exit RSI2 > 60 (ffill state) | + close > SMA200 — *"low-RSI-in-downtrend is a falling knife"* |
| `dual_mom` | 252d return > **6%** (T-bill proxy) | relative leg is the scanner's job |

### 7.4 Walk-forward gate (`walkforward.py`) — the money gate
Fold i: **choose parameters on 400 bars (warmup), execute only on the next
252 (test_days)**; windows never overlap, folds never shuffle; need ≥ 2 folds
(`n ≥ warmup + 2·test_days`). Grid capped at **12 combos** (refuses larger).
PASS requires ALL:
1. every fold's IS metric ≥ **min_is = 0.3** (default metric sharpe) — and
   computable (a fold with zero trades IS NaN → fails);
2. mean OOS/IS ratio ≥ **min_ratio = 0.5** over folds where it's computable;
3. **plateau**: at the modal fold choice, run the full history grid; ≥
   **min_plateau = 0.5** of the OTHER grid points must reach ≥
   `(1 − plateau_tol=0.35)` × the chosen setting's full-history metric —
   *"a strategy that works at exactly one setting is a fit, not an edge"*;
4. no computable ratio anywhere → FAIL, loudly (`reasons` list explains each).
Exit codes: 0 pass / **2 strategy failed** (a result, not an error — CI keys
on it).

Example: `gate RELIANCE --strategy trend_sma200 --param period=150,200,250`
→ 4 folds on 2,800 bars; if the 200 setting wins 3/4 folds and the 150/250
full-history shapes each hit ≥ 65% of 200's sharpe → plateau OK.

### 7.5 `research/validate.py` — the older year-based walk-forward
Score = CAGR/|maxDD| hybrid (guarded, ≥30 bars); windows default 3y train /
1y test / 1y step, min 250 train + 100 test bars per fold;
`overfit_ratio = OOS/IS` — *"< ~0.5 is suspicious, < 0 is dead"*.
`parameter_plateau` reports best/median/worst/frac_positive across a grid —
*"pass only if the plateau is broadly positive, not a single sharp peak"*.
(CPCV = Phase-3 upgrade, not built.)

### 7.6 Portfolio backtester (`backtest/portfolio.py`) — shared cash, concurrent positions
- One cash pool, `max_positions=5`, per-entry cap `max_alloc_frac=0.20` of
  the **regime-scaled budget**, `risk_per_trade=0.01`, stop 3·ATR14.
- **No-lookahead, three explicit rules**: decisions on bar i's close act at
  i+1's open; the regime scale used on day t is computed from bars **strictly
  before t** (`searchsorted(side="left")−1`); marks use each symbol's last
  close on/before the day, never a future bar. Entry budget basis = equity at
  YESTERDAY's marks (`equity_prev`), not today's.
- Slot contention resolves by (6m momentum desc, −vol desc) — *"deliberately
  simple"*.
- Regime breadth inside the engine counts only universe symbols (bench not
  included) and applies `classify()` per day.

### 7.7 Monte Carlo (`backtest/montecarlo.py`)
Bootstraps the CLOSED, COSTED trade PnLs with replacement (never resamples
prices, never invents trades), 1000 sims, fixed seed 12345 → final p5/p50/p95,
prob_loss, median and worst-5% drawdown. **< MIN_TRADES = 20 closed trades →
refuse (None)**: *"refuse to bootstrap on < 20 trades (this is an unknown, not
a pass)"*. The docstring states its own assumption limit: trade exchangeability
isn't strictly true in regimes — *"measures parameterisation risk, not a
crystal ball"*.

---

## 8. Sizing & risk (live side) (`portfolio/sizing.py`, `portfolio/limits.py`, `research/events.py`)

### 8.1 Position size
`qty = min(qty_risk, qty_alloc, affordable)` where
`qty_risk = risk_per_trade(0.01)·equity·(1−corr_penalty) / (3.0·ATR14)`,
`qty_alloc = max_alloc_frac(0.10)·equity/price`, and cash-affordability via
`buy_qty_for`. Refusals: non-positive inputs → qty 0 `invalid inputs`;
`3·ATR ≥ price` → `stop wider than price`.
Example: ₹10L equity, ATR14 = ₹90 → risk ₹10,000 / ₹270 per-share risk = 37
shares; alloc cap 10% at ₹2,400 = 41 → **37 shares, reason "risk"**.
`correlation_penalty`: max |Pearson| vs existing holdings over ≥ 60 joint
sessions; below **0.7** threshold → 0; above, ramps linearly to **0.5** at
corr 1.0 (shrinks size at most in half, never blocks).

### 8.2 Hard limits (`RiskManager.check_new_position`) — raise `RiskBlocked` with a human sentence, in order
1. kill_switch armed → *everything* blocked until manually disarmed;
2. mode == live AND realized today ≤ −`max_daily_loss ₹20,000` → no new
   positions today (paper mode is NOT blocked on purpose — the numbers there
   are practice);
3. symbol already held ≥ `max_per_symbol=1`;
4. open positions ≥ `max_positions=15`.
Dead-man: `no_stale_data(latest_bar, today, max_stale_days=3)` — trading on
data older than 3 days raises "Fix ETL before trading".
The CLI scan's own dead-man is stricter-vs-calendar but looser-by-default:
last NIFTY bar older than `--max-stale 5` **business days** → refuse,
`--ignore-stale` exists but is labeled *"(not recommended)"*.
*"Bypass is not a supported code path."*

### 8.3 Earnings size rule (`research/events.py`)
`NO_ENTRY_WITHIN = 2` sessions → allowed qty 0, note `NO fresh entry (plan
rule)`; `HALF_SIZE_WITHIN = 5` → `max(1, qty//2)`, note `half size (plan
rule)`. **Unknown results date → unrestricted but the note says "no event
guard active (fetch the calendar)"** — absence never silently blocks a trade
and never silently permits an unguarded one. Holiday mismatch (event date not
in the session list) falls back to `days·5/7` calendar distance. Example:
results 3 sessions out → 500 shares becomes 250 with the reason printed.

---

## 9. Journal, plans, fills (`execution/planlog.py`, `models.py`, `cli.py`)

- Three tables, one law: *"If it's not here, it didn't happen."*
  signals / plans / ledger. Plans carry a **mandatory written rationale ≥ 10
  characters** (CLI and Telegram both enforce — *"no reason, no plan"*).
- A BUY plan must satisfy `0 < stop < entry < target` or it's refused with the
  actual values echoed back.
- **TTL**: `expire_stale` marks open plans `stale` after **2 days**
  (`OrderPlan.ttl_days = 2` — *"a stale signal is a cancelled signal"*);
  `cmd_plan` runs it every time you add a plan; the weekly review counts them.
- Fills only execute **OPEN** plans (`fill --plan ID` refuses a non-open plan
  by name) and mark it `executed`; the CLI then reports **slippage vs plan
  entry** (`(px−entry)/entry`) and a planned-vs-filled qty difference — the
  plan-vs-actual loop is measured, not vibes.
- Journal gap check (dashboard + review): an EQUITY fill with no executed plan
  behind it is named — `X: fills without an executed plan` — while MF SIP rows
  and corporate-action synthetics are labeled (not flagged) as exempt.
- `executed_positions()` (net qty from executed plans) is the journal's INTENT
  view; `broker/reconcile.py` diffs it against Fyers holdings: MISSING AT
  BROKER / UNJOURNALLED / QTY MISMATCH — *"every divergence is reported to a
  human — this module never splits the difference and never trades to fix
  anything"*.

---

## 10. Money math (`portfolio/`)

### 10.1 FIFO tax engine (`tax.py`, Finance Act 2024 slabs, as-of 2026-11-01)
- holding **< 12 months → STCG 20%**; ≥ 12 → **LTCG 12.5%**;
  `months_between` decrements the count when the sell day-of-month < buy day.
- FIFO matching (the accepted method for equities); a sell exceeding holdings
  RAISES naming symbol/date/qty — the ledger can't go negative.
- Costs: sell-side costs reduce the gain pro-rata, buy-side costs increase the
  basis pro-rata (`buy_cost·take/lot_qty + sell_cost·take/sell_qty`).
- **LTCG exemption ₹1,25,000 per FY (April–March)**, applied only to positive
  LTCG (`max(0, ltcg − exemption)`).
- `tax_summary` (default) reports the **no-set-off upper bound** ("losses
  reported, not set off"); `tax_summary_with_setoff` runs the chronological
  ITR-order walk: intrayear STCG loss absorbs LTCG gain (statutory 111A→112)
  but **not vice versa**; brought-forward STCL absorbs STCG then LTCG; LTCG
  losses absorb LTCG only; losses carry **8 years** and age by CALENDAR years
  even across gap years (*"a loss must not survive 8+ years just because the
  book was quiet"*); expiry amounts are reported per FY. Every simplification
  is documented in the module header — computation, not advice.
- Example: FY with STCG −₹40k and LTCG +₹90k → intrayear: STCG 0 / LTCG
  50k; exemption 50k used → taxable LTCG 0; nothing carries (the consumed STCL
  is gone).

### 10.2 `open_lots` / harvest (`harvest.py`)
Same FIFO traversal as `realize` — *"harvested candidates and realized P&L
can never disagree about which lots exist"*. Three labeled candidates:
`loss-harvest` (tax saved = rate × loss, negative tax_effect),
`free-basis-reset` (LTCG gain still inside the ₹1.25L remaining exemption —
cost 0), `taxable-basis-reset` (cost = taxable part × 12.5%). Sorted by tax
effect. **Lots with no mark are SKIPPED AND NAMED** — *"a candidate list
computed on missing marks would be guesswork about guesswork"*. MF rows are
excluded from equity harvesting (debt slab rules differ — reported, skipped).

### 10.3 XIRR (`xirr.py`, `valuation.py`)
Bisection over XNPV on real dated flows (negative = invest, positive =
receive); bracket [−99.99%, +1000%], expands the high side ONCE, then gives up
honestly (None = "no root in bracket", reported with that sentence — e.g.
all-positive flows). < 2 flows → refuse. **Any held symbol without a stored
close → `missing_marks`, refuse to guess** — *"a wrong denominator poisons the
whole rate"*. BUY flows include costs; SELL flows net of costs; held value =
qty × last stored close (MF: last stored NAV; wrong source is worse than no
source → None).

### 10.4 Corporate actions on the ledger (`actions.py`) — manual-entry by design
*"The engine never infers corporate actions from price jumps and never guesses
a payout"* — kinds are only `dividend|bonus|split|redemption`, each with its
refusal: bonus/split need positive qty_delta; dividend/redemption need positive
cash; redemption additionally needs units and synthesizes its own SELL at
(cash+tds)/units — *"record ONLY the action, not a fill too (double-entry
would double-count the exit)"*. Split rescaling: pre-split BUYs scale qty×r,
price÷r keeping total cost identical (r implied by qty_delta against pre-split
bought units); bonus = zero-cost lot with holding start = the action date.

### 10.5 Mutual funds (`mf.py`)
AMFI `NAVAll.txt` only (reachable where NSE/Yahoo aren't; header contract
enforced §1.6); `MF:` prefix so scheme codes never collide with exchange
symbols; fractional units allowed (fill-mf: units/NAV > 0, costs ≥ 0). Debt
funds: the tax engine is equity-only, the rebalance lens prints
`DEBT funds -> slab-rate rules, engine does not model` instead of guessing a
fake 12.5%.

### 10.6 Rebalance (`rebalance.py`)
`tol = 0.02` no-trade band (fraction of TOTAL value); `max_new = 0.25` is a
**flag, not a clamp**: a brand-new name (not held yet) with a target above 25%
still gets its full proposal, annotated
`target X% > max-new 25%, staged entry needed` — the human decides to stage it;
target weights must be in [0,1] and **sum ≤ 1** or the CLI refuses (residual =
cash, and a residual > tol prints as a CASH HOLD row). Unknown mark → sized 0 with an explicit
note (`held, mark unknown — fetch NAV/bars before rebalancing`; `new name, no
mark — sized 0 (never guessed)`), overweight-with-no-mark still reports the
₹ estimate with `sized 0`. SELL rows carry `tax on realized lots: run tax
--setoff`. Output ends: *"proposals only — human-confirmed execution is the
standing rule"*.

### 10.7 Weights (`weights.py`)
`inverse_vol` default (no optimizer, no matrix inversion): `w_i ∝ 1/σ_i`,
per-symbol cap **0.25** with iterative proportional redistribution; zero
variance series → refuse (infinite weight guard); < 60 joint sessions →
*"refusing to size on thin air"*. HRP only via PyPortfolioOpt — any unusable
state (missing OR version-broken) raises ImportError and the CLI-level helper
**falls back to inverse_vol while PRINTING the downgrade**:
`"inverse_vol (fallback)"` is returned as the method actually used.

---

## 11. Evidence monitors & alerts

### 11.1 Triage vocabulary (`data/announce.py`) — one vocabulary, three users
Word-boundary, case-insensitive, **RED before AMBER** (precedence):
- **RED**: pledge encroach, encroachment, forensic, fraud, nclt, insolvenc,
  bankrupt, moratorium, suspended from trading, derecognition,
  wl (blacklisted), blacklisted, criminal complaint, money laundering.
- **AMBER**: resignation, resigns, credit rating, downgrade, delisting,
  delist, buyback, right shares, preferential issue, qdmit, qmmit, recording
  of order, upliftment, pledge, promoter sale, open offer, results, board
  meeting.
Headlines cannot invent categories — `data/news.py` pipes Google News RSS
titles through `triage_title`, the SAME function as exchange announcements,
so "flagged" means one thing everywhere. Anything matching no rule is
IGNORED, not summarized — *"that is what keeps it signal"*. Triage runs on
**holdings only** (empty book → empty alert, never a market-wide firehose;
macro news rides in under the explicit `MARKET` name). Windows: announcements
30d default, news 7d CLI / 3d briefing+dashboard.
"no rule matched" prints as: *"nothing flagged on holdings — that IS the
all-clear"* (ann) vs the news version: *"quiet **or feed down** — check fetch
status"*. News fetch: ≤ `MAX_SYM_QUERIES = 20` per-symbol queries + MARKET,
rows missing title/link counted as `skipped_rows`, never dropped silently,
per-link idempotent upsert.

### 11.2 Shareholding tripwires (`research/shareholding.py`) — only on present numbers
- promoter Δ < **−2.0pp** → `promoter -X.XX%`; FII Δ < **−3.0pp** → `FII
  exit`; pledge Δ > 0 → `pledge +X.XX%` — quarter-over-quarter, **only
  consecutive stored periods** (*"no fabricated quarters"*), NULL columns skip
  (deltas require both sides). Example: promoter 52.1 → 49.8 = −2.3 → flagged;
  −1.9 → not.
- Live payloads are stored RAW first; the normalized table fills only from the
  CONFIRMED pattern shape (`date/pr_and_prgrp/public_val`) or by hand
  (`--set SYM@PERIOD:promoter:fii:dii:public:pledge`); FII/DII/pledge aren't
  in the live payload, so the live path leaves them NULL rather than inventing
  zeros.

### 11.3 Health gate (`cli.cmd_health`) — a GATE, not a report; exits 1
Per universe symbol vs the NIFTY session calendar:
`missing_sessions > 2` (`--max-missing`) OR `staleness > 3` (`--max-stale`)
OR no data at all → problem → **exit 1** — *"a missing candle can fake a
trend"*. Missing is computed WITHIN the symbol's own first..last span (not
listed yet / delisted is unknown, not missing). adj_coverage = fraction of
sessions with a stored factor (ffill'd). The **split-gap check (§2.4) is a
loud print, NOT a gate** — round-4 proved 100% adj coverage can coexist with
an unexplained −50% cliff; a gap here means NO source explains the day.

### 11.4 Dashboard (`web/dashboard.py`) — read-only by construction
*"the request handler contains no write path, no subprocess, no order
anything"*; two GET endpoints (`/`, `/api/status`); stdlib http.server only;
binds **127.0.0.1** — `--host` beyond loopback prints an explicit no-auth
WARNING (the sanctioned case is the Docker service with an operator-controlled
port map); per-request connections (sqlite is thread-bound); page auto-refresh
300s; snapshot errors come back IN the JSON envelope so the page keeps working.
Honest-absence displays, each with its "says one vs the other" rule:
- no NIFTY bars → banner *"nothing below is trustworthy"*;
- banner STALE when last NIFTY bar > 5 business days old; PAUSED flag shown;
- heat grid: ● bar present / ○ no bar on an index session / **blank =
  pre-listing — never a fake miss**;
- split-gap ledger all-clear line exists; holiday drift ledger names disputed
  days or says "no drift days", and prints *"holiday master not fetched —
  drift unknown, not zero"* when the master is missing;
- UNRESOLVED corporate actions = stored ratio-NULL **and split-like** ("a
  dividend with no ratio is complete, not unresolved");
- news: no table at all → *"news not fetched yet"* vs table with no RED/AMBER
  → counted as genuinely quiet — *"saying one when it's the other is the
  dead-feed-as-quiet lie"*;
- sparkline: < 2 points or a FLAT series → muted dash — a flat line pretending
  otherwise is the dead-feed-as-quiet failure;
- `clean-day streak: not persisted — count [etl-loop] lines in the loop log` —
  the page NAMES the missing evidence instead of fabricating a counter.

### 11.5 Decay monitor (`research/decay.py`)
Paper book vs the strategy's OWN costed-backtest bootstrap band (p5/p95, 2000
sims, k = number of paper trades so far; rolling windows default 20, capped
at 8 window prints). Baseline < 20 trades → **None = "cannot judge"**, exit 1
— never a pass/fail on noise. `below_p5_all` → status
`REVIEW — below own expectation band` + the instruction: *"review = decide,
not auto-trade"*.

### 11.6 Telegram
Outbound (`telegram.py`): plain text (no Markdown — symbols like `RELIANCE^`
break parsers), ≤ 4000 chars, **one polite retry on 429** honoring
`retry_after` capped at 10 s, never raises — returns `(False, reason)`.
Inbound (`commands.py`): long-poll only (`getUpdates`, 35 s hold) — no webhook,
no open port, *"no URL to attack"*; **chat-id pinned BEFORE parsing** — an
update from any other chat is dropped with no reply, no signal; 409 conflict
prints the exact fix (deleteWebhook / fresh token) and backs off 30 s; every
loop survives any exception (sleep 2–3 s and continue). `/fill` records a
human-executed trade; `/plan` requires the same ≥ 10-char rationale and
`stop < entry < target` as the CLI.
Pause = a flag file (`DATA_DIR/paused.flag`) — survives restarts, visible with
`dir`, no schema to trust; settable via CLI or `/pause`.

---

## 12. AI layer (`ai/`) — annotation, never a dependency

- `AI_PROVIDER_ORDER` default `"ollama,none"`; unknown names skipped; a Null
  provider is always appended so "no AI" is the designed bottom, not a crash
  (`cmd_ai` then prints the deterministic factpack only).
- Providers are thin HTTP, **no SDKs**, 30 s timeout, `None` on ANY failure —
  *"callers must handle absent AI gracefully"*; the deterministic pipeline
  never depends on this (module header).
- Strict JSON contract: verdict ∈ {BUY, HOLD, SELL} else **UNPARSEABLE**
  (confidence 0, the raw text kept as the summary); confidence clamped 0–100.
- Grounding rules embedded in the system prompt: use ONLY factpack numbers,
  every statement traceable to a named field, label the data date,
  *"keep confidence honest: near 50 when evidence is mixed, not theatrical"*.
- The factpack itself is computed locally (close, RSI14, SMA50/200, 6m ret,
  ATR14, quadrant, regime, announcements) — the model annotates numbers the
  repo already proved; it cannot introduce a new figure.

---

## 13. Brokerage / broker (`broker/`)

- Adapters hold READ-scoped credentials, env only; NO order endpoint exists in
  the package, so placing an order is not a thing the code can do wrong.
- Parser tolerance rules (`parse_fyers_holdings`): envelope must say `s: ok`
  (else raise with the payload), zero-qty rows dropped, `NSE:RELIANCE-EQ` →
  `RELIANCE` (namespace + suffix stripped, hyphens inside BAJAJ-AUTO kept),
  unparseable rows skipped.
- Fyers token is a day-token — expected to be missing; `reconcile` prints the
  setup hint rather than crashing.
- Reconcile (§9) never "fixes" divergence by trading; at most it sends the
  report to Telegram (unless `--report-only`).

---

## 14. Cross-cutting numeric summary (one grep-proof table)

| Constant | Value | File |
|---|---|---|
| `SPLIT_JUMP` | 0.35 | research/splitgaps.py |
| split-step covered bands | [0.4, 0.7] ∪ [1.4, 2.5] | research/splitgaps.py |
| factor double-count tol | 0.02 | data/store.py |
| `NEW_LISTING_MIN_ROWS` | 60 | scheduler/etl.py |
| freshness settled cutoff | 16:15 IST | scheduler/etl.py |
| bootstrap `fresh_bdays` | 3 | scheduler/etl.py |
| loop window | 16:05–23:00 IST weekdays | scheduler/run_daily.py |
| loop retry / day-sleep | 60 s / 600 s | scheduler/run_daily.py |
| `--start` default | 2015-01-01 | scheduler/etl.py |
| provider pause | 0.3 s | scheduler/etl.py |
| workers | 5 | scheduler/etl.py |
| jump warn threshold | \|Δclose\| > 35% | data/base.py |
| gap warn | > 3×4 days | data/base.py |
| `LIQUIDITY_FLOOR_TURNOVER` | ₹2e7/day, 20 sessions | research/scanner.py |
| min evidence bars | 210 | research/scanner.py |
| QUADRANT_SCORE | I 2.0 / L 1.5 / W 0 / Lg −1 | research/scanner.py |
| COMP_RANGE_ATR / COMP_EMA_DRIFT / COMP_LOOKBACK | 0.90 / 0.03 / 20 | research/scanner.py |
| regime ON/OFF/NEUTRAL scales | 1.0 / 0.30 / 0.60 @ 0.60/0.35 | research/regime.py |
| FTD constants | 5%, day 4, 25 watch, 300 lookback, clamp(1.5σ,1%,2%) | research/regime.py |
| bulk boost | +0.5 iff > 0, 7-day window | scanner + feeds |
| results rule | no-entry ≤ 2 sessions, half ≤ 5 | research/events.py |
| shareholding tripwires | promoter −2pp / FII −3pp / pledge + | research/shareholding.py |
| slippage | 5 bps/side | backtest/costs.py |
| STT / stamp / SLBM / txn / SEBI / GST | 0.1%×2 / 0.015% / 0.003% / 0.00297% / 10 Cr⁻¹ / 18% | backtest/costs.py |
| backtest risk vs live | 2% vs 1%, both 3·ATR14 stops | engine / sizing |
| `max_alloc` | 0.10 single / 0.20 portfolio | engine / portfolio |
| fold sizes / gate floors | warmup 400, test 252, IS ≥ 0.3, ratio ≥ 0.5, plateau ≥ 50% within 35%, grid ≤ 12 | backtest/walkforward.py |
| `TRADING_DAYS` | 252 (weights: 250) | engine / weights |
| `MIN_TRADES` bootstrap | 20 | backtest/montecarlo.py |
| tax | STCG 20% < 12m, LTCG 12.5%, exemption ₹1.25L, carry 8y | portfolio/tax.py |
| `risk_per_trade / corr threshold / max penalty` | 1% / 0.7 / 0.5 | portfolio/sizing.py |
| `max_daily_loss / max_positions / max_per_symbol / no_stale_data` | ₹20,000 / 15 / 1 / 3 days | portfolio/limits.py |
| scan dead-man | 5 business days | cli.py |
| health gate | missing > 2, stale > 3 | cli.py |
| plan TTL / rationale min length | 2 days / 10 chars | planlog / cli |
| weights cap / min joint obs | 0.25 / 60 | portfolio/weights.py |
| rebalance tol / max-new (flag) / target sum | 0.02 / 0.25 / ≤ 1.0 | portfolio/rebalance.py |
| constants freshness | 10 months | tools/check_freshness.py |
| backup retention | 14 per stem | scheduler/backup.py |
| news cap / ann windows | 20 queries / 30d & 7d & 3d | data/news.py |
| XIRR bracket | [−0.9999, 10], widened once | portfolio/xirr.py |
| dashboard bind / refresh | 127.0.0.1:8765 / 300 s | web/dashboard.py |
| HTTP timeouts | 15/20/30/1.5 s (feeds/broker/AI/ollama-probe) | http, fyers, providers, router |

---

## 15. What is deliberately NOT here (verified absences)

Reading every file, these have NO implementation anywhere — by design, listed
so nobody "discovers" them later and guesses:

- any order placement / modification / cancellation (API, broker endpoint, or
  Telegram command);
- intraday or minute data of any kind; short selling; leverage; F&O;
- a second independent trading calendar (BSE/RBI) — on the backlog, not faked;
- a persisted clean-day-streak counter (the dashboard says so in words);
- loss carry-forward beyond 8 years; inter-bucket set-off beyond the statutory
  111A→112 direction;
- debt-fund tax modeling (flagged, never estimated);
- AI-driven position changes of any kind.
