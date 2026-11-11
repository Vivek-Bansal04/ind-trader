# ind-trader — the layman's guide

*(No jargon, no code. The engineer's version is `README.md`. Written 2026-11-12;
if the two ever disagree, the code wins — but tell me, because that's a bug.)*

## The one-line answer to "when can I buy or sell?"

**This system will never buy or sell by itself — that's not a missing feature,
it's the foundation.** It does everything *around* a trade: decides what's
worth buying, explains why, sets the exit before you enter, tracks it after.
You place the order yourself in your broker app. Humans click, machines advise.

## What it actually is

A very disciplined researcher who works for you every evening on your own
laptop. Six parts:

1. **The Collector** — at 4:05 PM each market day it downloads closing prices
   for ~500 Indian stocks + the NIFTY index, news about companies you hold,
   exchange announcements, and the holiday calendar. Everything lands in one
   file on your laptop. Nothing in the cloud, nothing at a broker.

2. **The Trust Inspector** — before anything is used, it's checked: a missing
   trading day? a price that "fell" 50% overnight that is actually an
   unrecorded stock split? the holiday calendar disagreeing with reality?
   Anything suspicious is NAMED and SHOWN, never quietly averaged over.
   Garbage in = loud warning out.

3. **The Analyst** — reads only the healthy data and answers the questions
   that matter: *Is the market in a friendly mood?* (recommends 100 / 60 / 30%
   of your money in stocks), *Which stocks look strong and why?*, *Is anything
   bad happening in what I own?* (fraud keywords, promoter selling, rating
   cuts — the same red-flag vocabulary everywhere).

4. **The Planner** — every buy idea must exist as a written plan BEFORE it
   becomes real: entry price, stop-loss (where you were wrong), target (where
   you were right), and the reason. No plan = no trade. This is what kills
   "felt like buying" trades.

5. **The Journal & The Page** — what you own, what it's worth, every plan and
   its status, on one web page that only ever reads and never changes
   anything. Your history can't be self-deceived about because it's written
   down.

6. **The Time Machine** — takes any rule ("buy when above the 200-day
   average") and replays it over years of history with real brokerage and
   tax costs, to show whether it WOULD HAVE made money.

## What is deliberately NOT built

- **Any connection that can place orders.** No button exists — not "not yet",
  never. The broker is read-only.
- **No intraday, no futures/options, no leverage.** Daily and weekly candles,
  delivery (CNC) buying, long only. Boring by design; boring survives.

## A normal day with it

- **Evening (automatic, zero effort):** the collector runs, the page
  refreshes, a short briefing arrives on Telegram.
- **Next morning (~10 minutes):** you look at the page. It says, for example:
  *market NEUTRAL → keep 60% of money in stocks; here are 5 shortlisted
  stocks with reasons; here is one plan: SBILIFE, buy near 3,120, stop 2,960,
  target 3,450, because weekly breakout.*
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
