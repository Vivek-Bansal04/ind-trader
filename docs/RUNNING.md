# Running ind-trader

## Where data lives

Everything persistent is in the **`data/` folder**, nothing else:

| File | What it holds | Format |
|---|---|---|
| `data/bars.sqlite` | All daily OHLCV candles for the universe (the single source of truth), plus ingest audit trail. Also holds the `delivery_pct` and `deals` evidence tables | SQLite |
| `data/plans.sqlite` | Signal log + order plans with mandatory rationale + status + `ledger` (actual fills for the tax engine) | SQLite |
| `data/universe.sample.txt` | Example watchlist: `SYMBOL<TAB>Sector` per line | text, git-tracked |

`.env` (copy of `.env.example`) holds secrets — **never commit it** (already git-ignored).
With Docker, the same `data/` folder is mounted into the container, so data survives
container rebuilds and is identical to the local-run layout. Backup = copy one folder.

## On your personal laptop (recommended — no proxy)

```powershell
git clone https://github.com/Vivek-Bansal04/ind-trader.git
cd ind-trader
python -m venv .venv                       # Python 3.12
.venv\Scripts\activate
pip install -e ".[dev,nse]"                # nse = NseIndiaApi primary provider

copy .env.example .env                     # add AI keys later when you buy them

pytest                                     # 45+ tests, no network needed
python -m indtrader.cli fetch RELIANCE     # real daily candles from NSE/Yahoo
python -m indtrader.cli backtest RELIANCE --strategy trend_sma200 --costs
python -m indtrader.cli backtest RELIANCE --strategy donchian --costs
python -m indtrader.cli rrg RELIANCE TCS INFY TITAN SUNPHARMA --bench NIFTY
python -m indtrader.cli scan                       # universe shortlist (evidence table + journal)
python -m indtrader.cli regime
python -m indtrader.cli ai RELIANCE        # works keyless (factpack) or with a model
```

Home network gives you BOTH providers: `nseapi` (NseIndiaApi → NSE's own archive,
includes delivery-era data) first, `yfinance` (Yahoo) as fallback. The chain is set by
`DATA_PROVIDER_ORDER` in `.env`; unavailable providers are skipped with a log line,
never a crash.

## With Docker (automation)

```bash
copy .env.example .env        # optional (Compose v2.24+): the stack runs without it
docker build -t indtrader .
docker compose up -d                                             # EVERYTHING: etl-loop + dashboard -> http://127.0.0.1:8765
docker compose run --rm etl-once                                   # fetch once now
docker compose up -d etl-loop                                      # auto-fetch every weekday 16:05 IST
docker compose run --rm cli backtest RELIANCE --strategy donchian --costs
docker compose run --rm cli rrg RELIANCE TCS INFY --bench NIFTY
docker logs indtrader-etl -f                                       # watch the scheduler
```

The loop is intentionally dumb: wake every 60s, fire once per weekday evening, sleep.
Upserts are idempotent, so a missed night self-heals the next evening.

## Manual daily workflow (what it's actually for)

1. 16:05 — ETL updates candles automatically, then evidence feeds (delivery % + bulk
   deals) best-effort (Docker loop or Windows Task Scheduler). `cli backup` in the same
   schedule (online sqlite copies + integrity check, keeps 14).
2. Evening — `cli regime` → one of ON/NEUTRAL/OFF sets your max exposure.
3. `cli scan` → ranked shortlist with evidence (trend/RRG/momentum/delivery/deals per
   symbol). Refuses (exit 1) if the index's last bar is >5 business days old — dead-man;
   fix the ETL, don't override.
4. For each pick: `cli compare <sym> --costs` to see all strategies side by side, then
   `cli plan SYM BUY --qty N --entry E --stop S --target T --reason "..." [--setup X]`
   — the rationale is mandatory and stop<entry<target is enforced at the door.
5. You place the CNC order manually at next morning's open (VRO or broker GTT/limit).
   The system never touches your broker.
6. After executing: `cli fill SYM BUY/SELL qty px --costs ... --plan ID` records the
   actual fill AND closes the plan (the ledger note keeps the `plan#N` trace);
   `cli reconcile` compares journal vs Fyers holdings (read-only) and
   flags anything unexplained. `cli tax` prints FY-wise realized gains + CGT estimate.

Monthly (plan §D discipline):

- `cli attribution` — realized P&L by setup × index-trend-at-entry; anything showing as
  `unplanned` is a fill without a written plan (visible, not hidden).
- `cli decay <sym> --strategy <s>` — the paper book vs the backtest's own bootstrap band;
  a REVIEW flag means put the strategy under human review, not that a bot trades less.
- `cli review` (Sundays) + `cli riskreport` close the loop; `cli dashboard` shows it all
  read-only on http://127.0.0.1:8765.

Seasonal (pre-March): `cli harvest` lists tax-loss candidates and free basis resets from
your open FIFO lots — computation only, the selling stays yours.

## Office laptop caveat (this machine)

Yahoo and NSE are both blocked by the corporate TLS-intercepting proxy; expect empty
fetches here (the ETL says so loudly and stores nothing — by design). Tests run fine
offline. Treat the office laptop as code-edit-only; run data work at home.
