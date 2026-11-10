# Running ind-trader

## Where data lives

Everything persistent is in the **`data/` folder**, nothing else:

| File | What it holds | Format |
|---|---|---|
| `data/bars.sqlite` | All daily OHLCV candles for the universe (the single source of truth), plus ingest audit trail | SQLite |
| `data/plans.sqlite` | Signal log + order plans with mandatory rationale + status | SQLite |
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
python -m indtrader.cli regime
python -m indtrader.cli ai RELIANCE        # works keyless (factpack) or with a model
```

Home network gives you BOTH providers: `nseapi` (NseIndiaApi → NSE's own archive,
includes delivery-era data) first, `yfinance` (Yahoo) as fallback. The chain is set by
`DATA_PROVIDER_ORDER` in `.env`; unavailable providers are skipped with a log line,
never a crash.

## With Docker (automation)

```bash
copy .env.example .env
docker build -t indtrader .
docker compose run --rm etl-once                                   # fetch once now
docker compose up -d etl-loop                                      # auto-fetch every weekday 16:05 IST
docker compose run --rm cli backtest RELIANCE --strategy donchian --costs
docker compose run --rm cli rrg RELIANCE TCS INFY --bench NIFTY
docker logs indtrader-etl -f                                       # watch the scheduler
```

The loop is intentionally dumb: wake every 60s, fire once per weekday evening, sleep.
Upserts are idempotent, so a missed night self-heals the next evening.

## Manual daily workflow (what it's actually for)

1. 16:05 — ETL updates candles automatically (Docker loop or Windows Task Scheduler).
2. Evening — `cli regime` → one of ON/NEUTRAL/OFF sets your max exposure.
3. `cli rrg ...` + scanner (Phase 2) → shortlist; each candidate has evidence attached.
4. For each pick: `cli backtest <sym> --strategy <s> --costs` sanity + written plan
   (`planlog.add_plan`) with entry/stop/target/rationale.
5. You place the CNC order manually at next morning's open (VRO or broker GTT/limit).
   The system never touches your broker.

## Office laptop caveat (this machine)

Yahoo and NSE are both blocked by the corporate TLS-intercepting proxy; expect empty
fetches here (the ETL says so loudly and stores nothing — by design). Tests run fine
offline. Treat the office laptop as code-edit-only; run data work at home.
