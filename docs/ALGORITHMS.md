# ind-trader — algorithms & formulas (as-built)

Purpose: a complete, honest specification of every decision rule in the system,
for review by a stronger model than the one that wrote it. Every constant here
was read from the code at commit `7f1701e` (2026-11-12); file paths point at
the implementation. The final section lists the questions we most want challenged.

Scope rules that constrain everything: daily bars only, long-only, delivery
(CNC), no leverage/F&O/intraday, no order placement anywhere. All formulas run
on **raw close prices adjusted by the factor layer in §2** unless stated.

---

## 1. Data pipeline (`data/ingest.py`, `scheduler/etl.py`)

- Providers tried in order: NSE library (`NseIndiaApi`) → yfinance; the serving
  provider is recorded per symbol (`provenance` column on stored candles).
- **Freshness skip rule** (`_is_complete_and_fresh`, nightly loop only):
  skip symbol iff ALL hold:
  1. `busday_count(last_bar, today) ≤ 3` (`fresh_bdays=3`);
  2. last bar is **settled**: last_bar ≤ today (before 16:15 IST also yesterday
     is unsettled) — a bar stamped for a live session always refetches (round-6
     froze 2026-11-11 bars at 14:14 IST intraday values);
  3. first bar ≤ start+45 days (default history start 2015-01-01) **or** a
     genuine new listing: first bar > start AND ≥ 60 rows
     (`NEW_LISTING_MIN_ROWS`, ~3 months; shallow-but-current is suspect).
- Result map semantics: `None` = skipped fresh, `0` = fetched-but-empty (loud),
  `>0` = rows stored. The nightly line prints all three separately (collapsing
  None/0 produced the false "EMPTY: all 499" alarm).
- Raw payloads of every external endpoint are archived verbatim before parsing;
  parsers only ever read CONFIRMED shapes (live-probed); unknown shape → store +
  report, never guess.

## 2. Price adjustment math (`data/store.py::combined_factors`)

- Raw candles are the source of truth; adjustment is a derived factor series.
- yfinance factor `b` = AdjClose/Close, re-anchored so the **newest day = 1.0**
  (yf convention); days before the oldest stored yf factor take that oldest
  level, never an assumed 1.0 (1.0 means "no adjustment" and would erase history).
- Recorded NSE splits (ex-date D, price multiplier r — e.g. 1:2 split → 0.5)
  merge **without double counting**: compute yf's own step on D,
  `step = b[D]/b[prev_day]`; if `|step·r − 1| ≤ 0.02` yf already priced the
  action → the recorded ratio adds nothing (counted as `skipped_covered`);
  otherwise r applies to days **strictly before D** (the ex-date's raw price is
  already post-split): `f[t] = b[t] · Π{ r_D : D > t }`.
- **Split-gap gate** (`research/splitgaps.py`, used identically by `health`,
  `coractions --gap-list` and the dashboard): every 1-day raw move with
  `|pct| ≥ 0.35` must coincide with a combined-factor step in [0.4, 0.7] ∪
  [1.4, 2.5] on the same day, else the symbol is named. A dividend-only step
  (~1.0) under a −50% cliff is the exact failure this catches.
- Ratio conventions (`research/coractions.py`, only numbers the exchange TEXT
  states, never faceVal, never a human's silence):
  - bonus `a:b` → price multiplier `a/(a+b)` (1:1 bonus → 0.5);
  - split ratio `a:b` with unknown notation direction → **the shrink**,
    `min/max` (equal → None, it's a no-op);
  - face-value / from-to phrase `From Rs X … To Rs Y` (≤3 words between) →
    `min/max`; routes anchored to `rs|inr` or `from…to` so date pairs
    ("from 01-Apr to 15-Apr") can never mint a ratio;
  - no numbers → stored as event, ratio NULL, kind split-like → **UNRESOLVED**,
    kept red until a human confirms (`coractions --set`, ratio required).
- Calendar precedence (`data/macrocal.py`): stored bar > holiday row (what
  happened beats what was planned); a holiday row WITH a bar = **disputed**
  (drift ledger), never resolved silently.

## 3. Indicators (`ta.py`) — exact formulas

| Indicator | Formula / convention |
|---|---|
| SMA(n) | simple mean, `min_periods=n` (NaN until n bars) |
| EMA(n) | `ewm(span=n, adjust=False, min_periods=n)` |
| RSI(n=14) | **Wilder's exact recursion**: seed = simple mean of gains/losses over first n; then `avg = (avg·(n−1) + x)/n`; flat market → **50**, not 100 (the audited-repo bug) |
| ATR(n=14) | TR = max(H−L, \|H−C_{t−1}\|, \|L−C_{t−1}\|); RMA smoothing (`ewm(alpha=1/n)`) |
| MACD | EMA12−EMA26, signal EMA9 of line (plain EWM, adjust=False) |
| Bollinger(20,2) | mid SMA20, bands mid ± 2·std(ddof=0) |
| Donchian(55) | rolling max(high)/min(low), no shift inside |
| AnnVol(20) | std(simple returns, 20d, ddof=0) · √252 |
| SuperTrend(10,3) | standard ratcheting ATR bands around HL2 |
| Weekly bars | derived: resample W-FRI OHLCV, **drop trailing partial week** |

## 4. Regime traffic light (`research/regime.py`)

- breadth = fraction of universe (≥200-bar names only) with close > own SMA200.
- `classify(breadth, index_above_200dma)`:
  - **ON** (exposure ×1.0) iff breadth ≥ 0.60 AND index > its SMA200;
  - **OFF** (×0.30) iff breadth ≤ 0.35 OR index ≤ SMA200;
  - **NEUTRAL** (×0.60) otherwise — and also when either input is UNKNOWN
    (missing data defaults cautious, never bold).
- `flip_note` prints the exact count/level needed to flip (anti-vibes rule).
- Follow-through (informational only, never scales exposure): 300-session
  state machine; correction starts at −5% off peak; rally day = daily change
  ≥ clamp(1.5σ, 1%, 2%) at day ≥ 4 of the correction; FTD fails if price closes
  below the rally low; reverts to uptrend if the FTD holds 25 sessions.
  < 60 bars → None (absent ≠ friendly).

## 5. Relative rotation (`research/rrg.py`) — clean-room

- RS = close_stock / close_bench (inner-joined, daily; rrg_table uses weekly).
- RS-Ratio = 100 · EMA₁₄(RS) / MA₁₄(EMA₁₄(RS)); RS-Momentum = 100 + 100 ·
  EMA₁₄(ROC₁₀(RS-Ratio)). Quadrants: (RSR≥100, RSM≥100)=Leading;
  (≥,<)=Weakening; (<,<)=Lagging; (<,≥)=Improving; NaN=NA.
- Transition events (Improving→Leading) are the actionable signal; scanner
  currently uses only the CURRENT quadrant, not the transition.

## 6. Scanner (`research/scanner.py`)

- Evidence requires ≥ 210 bars, else `tradable=False` with a bar-count note.
- Gates (hard): tradable = close > SMA200 **AND** liquidity floor met.
  Liquidity = mean(close·volume) over 20 sessions **before** the signal day
  ≥ ₹2e7/day; missing volume = unknown = NOT a disqualification (shown, never
  imputed).
- Score (soft, additive): quadrant score {Improving 2.0, Leading 1.5,
  Weakening 0, Lagging −1.0} + percentile rank of 6-month return (126d);
  +0.5 if net bulk buying positive (delivery % never boosts, only annotates
  "low delivery on trend" when delivery < min(own 20d avg, 40%) on a
  trend day). Earnings proximity annotates: ≤2d "no fresh entry", ≤5d "half size".
- Compression (annotation only, never gates): 20d close range / ATR14 ≤ 0.90
  AND |close/EMA21 − 1| ≤ 0.03 → "compressed base".
- Sort: tradable first, then score; regime scales exposure downstream.

## 7. Position sizing & risk (`portfolio/sizing.py`, `portfolio/limits.py`)

- qty = min(qty_risk, qty_alloc, affordable) where
  qty_risk = (risk_per_trade · equity · (1 − corr_penalty)) / (stop_atr_mult · ATR14),
  live defaults risk_per_trade = 1%, stop = 3·ATR14 (backtester default 2%).
- Limits (raise `RiskBlocked` with human text): kill switch; daily realized
  loss ≤ −₹20,000 → no new positions (live mode); ≤15 open positions; 1 position
  per symbol; **stale-data dead-man**: latest bar > 3 days old → refuse.

## 8. Planner & journal (`execution/planlog.py`)

- OrderPlan = (symbol, action, qty, entry, stop, target, created, reason, setup,
  status ∈ open|executed|skipped|cancelled); fills are separate timestamped rows
  (date, symbol, side, qty, price, fees, note). Holdings = net Σ(qty·±side);
  marks = last close (equity) / last NAV (MF) at read time — no valuation
  without a stored price (None, never invented).
- Dashboard flags "fills without an executed plan" (journal gap).
- **No order API exists anywhere in the codebase** — enforced by absence +
  tests; execution is human, at the broker, by design.

## 9. Backtest (`backtest/`)

- Engine: daily event loop; signal on bar i **executes at open of i+1**;
  slippage moves the fill price (±5 bps/side), not the cost line.
- Costs (as of 2026-11-01, conservative-high): STT 0.1% both sides, exchange
  txn 0.00297%, SEBI 10/crore, stamp 0.015% buy, SLBM 0.003% buy, GST 18% on
  (brokerage+txn+sebi), brokerage 0% (discount CNC; rate+₹200 cap knob exists).
- Exits: 3·ATR(14) trailing stop ratchets up (fill at min(stop, open) when
  gapped through), optional time stop, signal exit at close when signal=0.
- Sizing in-engine: risk 2% equity, cap 10% allocation (differs from live 1% —
  deliberate stress).
- Strategies (all gated long/flat): trend_sma200 (Faber); dual_ma 20/100 + 200
  gate; donchian 55/20 + gate; ts_mom(126d>0 + gate) (Moskowitz); rsi_reg
  (RSI2<10 buy, >60 exit, gate — long-only-in-uptrend, never falling knives);
  dual_mom (12m return > 6% T-bill proxy).
- **Walk-forward gate**: fold = 400-bar train → next 252-bar test, non-overlapping;
  grid ≤ 12 combos chosen in-sample; PASS requires: every fold IS metric ≥ 0.3
  (sharpe default), mean OOS/IS ratio ≥ 0.5, and plateau — ≥50% of full-history
  grid neighbours within (1−0.35) of the modal choice's metric. No computable
  ratio → FAIL. Untradeable OOS (0 trades) → FAIL, loudly.
- Metrics: CAGR, Sharpe (√252, ddof=0), max DD, profit factor, win rate.

## 10. Evidence monitors & alerts

- `health` gate (exit 1): missing sessions vs NIFTY calendar > 2; staleness > 3
  sessions; adjustment coverage; split-gap (§2) printed but not gating.
- News/announcements triage: RED = {pledge encroach(ement), forensic, fraud,
  nclt, insolvenc*, bankrupt, moratorium, suspended from trading, derecognition,
  blacklisted, criminal complaint, money laundering}; AMBER = {resignation(s),
  credit rating, downgrade, delist(ing), buyback, right shares, preferential
  issue, qdmit, qmmit, recording of order, upliftment, pledge, promoter sale,
  open offer, results, board meeting}. Word-boundary, case-insensitive,
  precedence RED first; headlines cannot invent categories. Dead feed =
  "unreachable" status, never a clean-looking zero.
- Shareholding tripwires (only on present numbers): Δpromoter ≤ −2.0pp,
  ΔFII ≤ −3.0pp, Δpledge > 0 (quarter-over-quarter, consecutive stored periods only).
- Telegram briefing + pause flag file; review/riskreport compose weekly text.

## 11. Known simplifications (flagged honestly)

1. Scanner RRG runs on **daily** closes; classical RRG is weekly (the repo's
   `rrg_table` is weekly but the scanner path is daily). m=14, k=10 uncalibrated.
2. Backtest is single-symbol; portfolio-level capital contention, correlation
   and cash competition are NOT simulated (portfolio.py exists but isn't wired
   into the engine loop).
3. Backtest risk 2%/10% vs live 1% — the stress is intentional but undocumented
   in results tables.
4. Liquidity proxy = close·volume (turnover), not real traded value; volume can
   be 0/missing on some NSE feeds (unknown ≠ disqualified).
5. Slippage flat 5 bps regardless of size/liquidity; no market impact model.
6. Regime is a step function (1.0/0.6/0.3); no vol-scaling; FTD is informational
   only (deliberate, but reviewable).
7. Marks are last stored close; intraday value is unknowable by design.
8. Ratio parsing covers observed NSE spellings only (round-7 corpus); new
   spellings fall to UNRESOLVED → human `--set`. By design, but it is a parser,
   not a proof.
9. The 200-DMA breadth gate counts only names with ≥200 bars — a young bullish
   cohort is invisible to breadth.
10. Costs fixed as of 2026-11-01 with a freshness checker, but no live schedule
    lookup.

## 12. Questions we want challenged (for the reviewing model)

1. Is the 4-step scanner ordering (regime → SMA200 → quadrant → 6m momentum)
   defensible on Indian large/mid-caps, or should breadth exposure be continuous
   rather than 3-state? Any published Indian evidence on 200-DMA vs alternatives
   (150/250, ATR-channel) for NSE swing horizons?
2. Split-gap gate bands ([0.4, 0.7] ∪ [1.4, 2.5] on ±35% jumps): are they too
   wide (missing a 1:1.3 sub-division?) — what ratio families do Indian
   bonus/split events actually take, and should the gate be exact-match against
   the corporate-actions table instead of a size band?
3. Walk-forward gate thresholds (IS sharpe ≥ 0.3, OOS/IS ≥ 0.5, plateau 50%
   within 35%): too lax/too tight for ~10 years of daily Indian data with the
   400/252 fold? Should folds use anchored (expanding) windows instead of
   rolling?
4. Wilder RSI(2)<10 mean-reversion INSIDE an uptrend on single Indian stocks —
   known failure modes (gap-downs through stops on event days)? Worth adding an
   earnings-blackout as a hard gate rather than the current annotation?
5. Risk: 1% + 3·ATR stop vs the literature claim that stops add little for
   indices but help single names (the engine docstring's own hedge) — what would
   a proper A/B on this dataset need to be convincing?
6. Is the no-double-count factor merge (§2) robust if yfinance's factor feed
   itself has a wrong ex-date by ±1 session? Would a cross-check against the
   split's book-closure dates add signal or noise?
7. Breadth denominator: should ≥200-bar filtering be replaced by "bars ≥ max(60,
   window)" with window-matched 200-DMA availability, or is excluding young names
   the lesser evil for a 500-name NSE universe?
8. Anything where you believe the fail-loud rules (unknown ≠ zero, settled tail,
   anchored regexes) still leave a silent-failure path we haven't named.
