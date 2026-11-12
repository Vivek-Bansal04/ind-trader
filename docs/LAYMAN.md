# ind-trader — the layman's guide

*(No jargon, no code. The engineer's version is `README.md`, and the exact
formulas — every threshold, fold rule and keyword list as built — are in
[`docs/ALGORITHMS.md`](docs/ALGORITHMS.md). If any of these ever disagree, the
code wins — but tell me, because that's a bug. Written 2026-11-12.)*

## The one-line answer to "when can I buy or sell?"

**This system will never buy or sell by itself — that's not a missing feature,
it's the foundation.** It does everything *around* a trade: decides what's
worth buying, explains why, sets the exit before you enter, tracks it after.
You place the order yourself in your broker app. Humans click, machines advise.

## What it actually is

A very disciplined researcher who works for you every evening on your own
laptop. Six parts — each one below says where its data comes from and how it
decides, in the same words the code uses:

### 1. The Collector

- **Where the data comes from:** daily closing prices for the
  ~500-stock universe + NIFTY come from **NSE's own public endpoints** (via an
  open-source NSE library); if that source fails, it falls back to **Yahoo
  Finance** and says which one served. Weekly bars are *derived* from the
  daily ones (week ending Friday, complete weeks only) — one source of truth,
  never two that can disagree. Company news comes from **Google News
  RSS** (no API key; fetched in a way that mimics a normal Chrome browser,
  because the plain method gets blocked on some networks). Exchange
  announcements, the official holiday list, shareholding patterns and
  bonus/split records come from **NSE's public data again**. Every response is
  also saved *exactly as received* (a raw archive), so any number on any page
  can be traced back to the exchange's own words.
- **How it decides what to believe:** nothing. The collector is deliberately
  dumb — it stores, it doesn't judge. Judging is part 2's job. A source that
  fails is reported as failed; an empty result is distinguished from "couldn't
  reach" — a dead feed never gets to look like a quiet market.

### 2. The Trust Inspector

- **Where the data comes from:** the collector's own archive — it inspects,
  it never fetches.
- **How it decides:** a checklist, each item producing a named suspect, never
  a silent fix.
  - *Missing days:* the index's own trading dates are the reference. A stock
    missing more than 2 sessions against that calendar, or whose data ends
    more than 3 sessions ago, is named.
  - *Fake crashes:* any one-day move of ±35% or more must be explained by a
    recorded split/bonus adjustment of the matching size. A 50% "crash" with
    only a dividend adjustment behind it is exactly the lie this catches
    (it caught a real one — HDFCBANK).
  - *Calendar disputes:* a stored holiday that nonetheless has a NIFTY bar is
    put on a drift ledger by name.
  - *Unrecognised shapes:* if NSE changes their data format, the new shape is
    stored and reported — the parser is only ever extended on a confirmed
    shape, never guessed.

### 3. The Analyst

Three separate jobs, none of them allowed to make up data:

- **The market traffic light** — *inputs:* only the inspected closing prices.
  Two numbers decide: what fraction of the universe trades above its own
  200-day average (**breadth**), and whether NIFTY itself is above its
  200-day average. **ON** (full exposure) needs breadth ≥ 60% *and* NIFTY
  above its average; **OFF** (cut to 30%) fires if breadth ≤ 35% *or* NIFTY
  is below; in between (or if either number is unknown) it's **NEUTRAL**
  (60%). It also prints the exact levels that would flip the light, so the
  mood is arithmetic, not vibes.
- **The stock shortlist** — one evidence row per stock, ranked in a strict
  order: (1) if the light is OFF, nothing is interesting; (2) the trend
  gate — the stock must be above its own 200-day average, else full stop;
  (3) relative strength vs NIFTY via the rotation quadrant (stocks turning
  *upward* relative to the market score highest); (4) 6-month momentum
  rank. Volatility is shown but never scored — size handles volatility,
  selection doesn't. Every row carries its numbers so you can argue with it.
- **The bad-news watcher** — headlines and exchange announcements about
  *what you own* pass a fixed vocabulary: RED words (fraud, forensic audit,
  insolvency, pledge encroachment…) and AMBER words (resignation, rating
  downgrade, delisting, results…). A headline cannot invent a new category.
  Shareholding adds three tripwires: promoter holding down 2%+ in a quarter,
  foreign investors pulling out 3%+, or pledge rising — each only if the
  number is actually present; missing means missing, never zero.

### 4. The Planner

- **Where the data comes from:** the analyst's shortlist + your account
  size (you tell it once) + the stock's own volatility.
- **How it works:** an idea becomes a **plan** record with entry, stop,
  target, and a written reason *before* any order exists. The quantity is
  arithmetic, not courage: risk 1% of your capital per trade; the stop sits
  3×ATR below (ATR = the stock's typical daily swing — a noisy stock gets a
  wider stop and therefore fewer shares, automatically); further caps shrink
  it if you already own similar stocks (correlation) or if it would exceed
  the position's allocation. The plan then waits — status `open` — until YOU
  either place the order yourself at the broker and record the fill, or
  mark it skipped. The system has no way to place it for you, by design.

### 5. The Journal & The Page

- **Where the data comes from:** the two local database files — one holding
  all collected market data (with the raw exchange archive), one holding
  every signal, plan, and fill ever recorded, timestamped.
- **How it works:** it renders exactly those records, with nothing invented:
  empty sections say "not fetched yet" or "none", stale data says STALE, and
  the split-gap/unresolved ledgers stay visible until the underlying facts
  are fixed. The page physically cannot change a record — it opens the
  database read-only.

### 6. The Time Machine

- **Where the data comes from:** the same inspected history (and it refuses
  stocks with unexplained price cliffs rather than scoring on broken data).
- **How it works:** replay. A rule (say "hold above the 200-day average,
  exit below") is run day-by-day over years as if it had been followed,
  with real Indian costs: brokerage, STT, stamp duty, GST, slippage. The
  honest part is *walk-forward*: the rule is tuned on an early window and
  tested only on later, unseen data — the same way trading it live would
  have felt, not the fantasy version where you tune on the test itself.

## What is deliberately NOT built

- **Any connection that can place orders.** No button exists — not "not yet",
  never. The broker is read-only.
- **No intraday, no futures/options, no leverage.** Daily and weekly candles,
  delivery (CNC) buying, long only. Boring by design; boring survives.

## A normal day with it

- **Evening (automatic, zero effort):** the collector runs, the page
  refreshes, a short briefing arrives on Telegram. If a stock YOU own has its
  quarterly results within 4 days, or genuinely good news (orders won, record
  profit, buyback…) shows up without a warning mixed in, that also arrives on
  Telegram — once, never repeated.
- **Your holdings, always on screen:** record what you already own once (the
  "My holdings" form on the page, or `indtrader fill`) and the page keeps the
  list: what you paid, what it's worth, the results date counting down and the
  latest headlines per name. Recording means "I actually bought this" — the
  form writes your journal, it never places an order.
- **Next morning (~10 minutes):** you open the page. It leads with a plain
  verdict — *market light NEUTRAL: new buying would use up to 60% of your
  money* — then "Your moves today": one card per open decision (*SBILIFE, buy
  near 3,120, stop 2,960, target 3,450, because weekly breakout — you decide;
  if you buy, record it with this command*), plus cards for anything that
  needs fixing first. All the technical tables sit in dropdowns underneath.
  You decide. Agree → place the order yourself in your broker app → tell the
  system "filled at 3,124". Disagree → say skip, move on. From then on the
  system watches the stop and the target for you.

## Why it can't just "go live" tomorrow

A signal computed on a stock whose history contains a fake 50% crash is a lie
with a nice chart. So before real money, three things get proven, in order:

| Stage | What gets proven | Roughly |
|---|---|---|
| Data fix | the 58 stocks with old-split holes in their history get proper adjustment factors | this week |
| The habit | 20 trading days where the nightly collection simply works, evidenced daily | ~Dec 10 |
| The strategy | rules replayed on history + paper trading: the system gives real daily plans, you track but DON'T buy, and see what would have happened | ~Dec |
| Real money | small sizes, every rule enforced, you place every order yourself | realistic: January, sooner if the evidence rushes |

Every stage leaves written evidence (test counts, health ledgers, paper-trade
journal), so "trustworthy" here is a reading, not a feeling.

## The part that never gets finished

At every stage, to the last decimal: **you press the button.** The system's
ambition is to make you the boring, documented, hard-to-ruin investor —
decisions written before they're made, exits written before entries, and a
paper trail that outvotes your mood. The brokerage app remains the only place
money moves.
