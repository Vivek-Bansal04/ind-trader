# ind-trader — how data fetching actually works

Written 2026-11-12 in answer to plain questions ("what if I miss two days?",
"does it re-download 15 years every night?"). This is the non-math version of
`docs/ALGORITHMS.md` §1 — same rules, explained in words, with the incidents
that caused each one. The code wins if they ever disagree.

---

## The short answers

| Question | Answer |
|---|---|
| How much history do we keep? | From **2015-01-01** onward, forever. Nothing is ever trimmed. ~11 years of daily bars for ~499 names ≈ 1.4M rows ≈ 150–200 MB. |
| Why not just the last 100 days? | Almost every analysis needs more: the 200-DMA trend gate (200 bars), scanner evidence (210), FTD (up to 300 sessions), walk-forward validation (400-bar warm-up + 252-bar test **per fold**). |
| Does it re-download 15 years every night? | On a healthy night: **no downloads at all** — fresh symbols are skipped without a network call. When a symbol *does* need fetching, the response does contain the full 2015→today history — see "why the whole history" below. |
| I missed Mon+Tue, opened Wednesday — what happens? | Wednesday's run notices every symbol is missing Mon+Tue and refetches. One pass, both days filled, evening numbers current. (This is round-8 behaviour; before the fix, the two days stayed missing until Thursday. See "the bug that question caught".) |
| Network died mid-run? | The loop prints the failure and retries every 60s until 23:00. Storage is per-symbol, so a half-finished run is safe: refetched symbols land, the rest get healed by the next run. |
| Laptop off for a fortnight? | One successful run fills all 14 days in one pass. Data intact; the clean-day streak restarts from zero — the streak measures the *habit*, so a gap is a gap. |

## What lives in the database

One SQLite file (`bars.sqlite`). Besides daily candles, it accumulates — all
of it history, none of it date-trimmed:

- daily OHLCV bars per symbol + the `provenance` column (which provider served each row);
- corporate actions (splits/bonus/dividends) and split ratios;
- price-adjustment factors (yfinance + recorded splits, merged in §2 of ALGORITHMS.md);
- the exchange calendar (which days the market was open) and the holiday-master drift ledger;
- news items, shareholding quarters, earnings dates;
- the journal: plans, fills, scan results (the paper-trading evidence);
- **raw provider payloads, archived verbatim before any parsing** — so a parser
  bug is always re-checkable against what the exchange actually sent.

Daily bars only. There is no intraday history anywhere, by design — the system
is built to never need it.

## One evening, step by step

For each symbol in the universe:

1. **Ask the store, not the internet:** what are this symbol's first bar, last
   bar, and row count?
2. **Is the tail current?** The tail must sit **on** the latest *settled*
   session the exchange calendar confirms (the calendar = the NIFTY bars
   already in the store; "settled" = not today before 16:15 IST, because a
   today-stamped bar is an intraday snapshot).
3. Current **and** deep enough → **skip. Zero network calls for this symbol.**
4. Anything else → **one request** to the provider asking for the full
   2015→today daily history for that symbol.
5. Whatever comes back is upserted: rows keyed by symbol+date, so 3,499
   identical rows re-write themselves harmlessly and the genuinely new rows
   (the missing days) arrive. Re-running is always safe — order and timing of
   runs never change the final data.

## "Why ask for 15 years when only 4 days are new?"

Because the *request* is one HTTP call either way — the API doesn't work per-day.
Asking for 4 days or 3,500 days is the same single download, roughly the same
size on the wire (~100–200 KB of JSON). Asking for everything costs nothing
extra and buys three properties that an incremental "give me just the new days"
fetch structurally cannot:

1. **Middle-hole self-healing.** An incremental fetch can only extend the tail.
   A session missing *in the middle* of a series (flaky provider responses have
   done this) would sit there, invisible, forever. A full re-fetch repairs it
   because the upsert fixes any date, not just the newest.
2. **Retroactive factor refresh.** A split or dividend *this week* changes the
   adjustment factors of *years* of past bars. Re-pulling history is how those
   get corrected; append-only fetches can never notice.
3. **Statelessness.** There is no "cursor" of where we left off that can drift,
   lie, or need its own watchdog. This is the same fail-loud philosophy as the
   freshness rule itself: prefer a dumb rule you can prove over a clever one
   you have to trust.

The honest cost: after a missed day, one evening's run refetches ~499 symbols
(one call each, 5 in parallel) ≈ 2–3 minutes. A healthy evening: under a few
seconds, because step 3 above skipped everything.

## What the skip rule used to be (and why it isn't anymore)

**The old rule:** skip if the last bar is within 3 business days of today.
The 3 was a weekend/holiday tolerance — but it meant a **1–2 session hole also
counted as "fresh"**. Miss one evening → the gap went unfilled until it grew
past 3 business days on its own. Self-healing worked only by accident, when
the hole became big enough to trip the constant.

**The bug that question caught** (round-8, 2026-11-12): asked "what happens if
I miss two days?", tracing the exact case through the code showed the design
claim in the README ("one successful run fills all missed days") was false for
a 2-day gap — Wednesday's run would call every symbol fresh and fetch nothing;
Thursday's run would heal it as a 4-day hole. The fix (commit `3b8939b`): the
store already holds the exchange's own calendar (the NIFTY bars), so ask the
correct question instead — *"does this symbol hold the latest settled session?"*
Any calendar-confirmed hole, of any size, refetches. Weekends and holidays
need no special case: NIFTY has no bar for them either, so Friday's tail is
fresh on Saturday morning and stale by Monday evening — exactly as it should be.

The old lenient rule survives in **one** situation: the very first bootstrap
run, when NIFTY itself has no stored bars yet, so there is no calendar to
anchor to. Once the index has bars, the calendar rule owns the decision.

**A round-5 incident worth remembering:** ~193 of 499 symbols (listed after
2015) failed the *old* completeness check every single night and re-downloaded
full history pointlessly — a re-fetch can't invent bars from before a company
existed — blowing the run past 2 minutes nightly. The skip rule gained the
"new listing" exemption that night: a late first bar plus ≥ 60 rows is a real
listing, not a hole. Shallow-but-current series (fewer than 60 rows) are still
refetched: that guard stays, because a *quietly shortened* series fakes a
backtest.

## Where the honesty rules bite

- **Skipped ≠ empty ≠ stored.** The nightly line prints all three: `None` =
  skipped-fresh, `0` = fetched but got nothing (loud), `>0` = rows stored.
  Collapsing them once produced a false "EMPTY: all 499" alarm; never again.
- **A dead feed never looks like a quiet market.** Unreachable provider =
  "unreachable" status. Unknown is never rendered as zero.
- **Downstream, the health gate** (missing sessions vs the NIFTY calendar,
  staleness) catches anything computed on a day the collector missed, before
  it can be presented as current. So "down at 16:05" degrades into "page says
  STALE tonight, data healed tomorrow" — never "page quietly shows yesterday
  as if it were today".
- **Backups** (`backup` command) keep the newest N snapshots of the database
  and delete older snapshot *files* — the pruning never touches the data
  inside the database.

## Numbers worth knowing (as of 2026-11-12)

- Universe: 499 NSE names, fetched with 5 parallel workers.
- History start: `2015-01-01` (a CLI flag, `--start`; the default is what runs nightly).
- Full-universe worst-case run ≈ 2–3 min; steady-state evening ≈ seconds.
- Freshness cutoff: 16:15 IST (market closes 15:30; buffer for the daily close
  to settle at the provider).
- Retry loop: every 60s from 16:05 until 23:00 IST; loop start runs immediately
  (so "boot up and it backfills" is the whole recovery story, and it is enough).
