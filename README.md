# ind-trader

Personal, non-intraday trading & investing platform for Indian equities (NSE/BSE).
Daily/weekly bars only — signals on closed bars, decisions in the evening, execution
next morning by hand, CNC/delivery only. **No intraday, no scalping, no F&O, no
leverage — there is no code path to any of them.**

Built from scratch (Nov 2026) after a source-level audit of 111 GitHub repositories
(including every fork on this profile). Every design choice is attributable — see
[Provenance](#provenance--what-came-from-where) below and `docs/ARCHITECTURE.md`.

## What it does

| Command | Purpose |
|---|---|
| `fetch RELIANCE` / `fetch-universe` | Download daily candles into the local store (NSE archive via NseIndiaApi, Yahoo fallback) |
| `backtest SYM --strategy X --costs` | Daily-bar backtest with real Indian delivery costs and next-bar-open fills |
| `rrg SYM SYM ... --bench NIFTY` | Clean-room Relative Rotation Graph: quadrant + quadrant transitions |
| `scan` | Universe shortlist: one evidence row per symbol (trend, RRG quadrant, 6m momentum, delivery %, bulk deals, vol), ranked; shortlist journaled to `plans.sqlite` |
| `fetch-feeds` | Nightly evidence refresh: delivery-% bhavcopy + bulk deals (NSE; keeps last-known when unreachable) |
| `notify` | Evening briefing (regime + shortlist): sends to Telegram when configured, prints otherwise |
| `reconcile` | Journal vs broker holdings (Fyers read-only token): reports mismatches, never acts on them |
| `universe --index "nifty 50" --out data/universe.txt` | Dump live index constituents to a universe file |
| `regime` | Market traffic-light (breadth + index trend) → recommended exposure 100/60/30% |
| `ai SYM` | Optional AI view. Works keyless (deterministic factpack); add any model key later |
| `run_daily` (Docker loop) | Nightly 16:05 IST ETL, idempotent upserts |

## Quickstart (personal laptop)

```powershell
python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev,nse]"
copy .env.example .env
pytest
python -m indtrader.cli fetch RELIANCE
python -m indtrader.cli backtest RELIANCE --strategy trend_sma200 --costs
```

Full guide including Docker automation: **`docs/RUNNING.md`**.

```bash
# Docker one-liner equivalent of the whole nightly pipeline
docker compose up -d etl-loop
docker compose run --rm cli backtest RELIANCE --strategy donchian --costs
```

## Where data lives

Everything is in the **`data/` folder** (git-ignored, Docker-volume-mounted):
`bars.sqlite` (candles + ingest audit), `plans.sqlite` (signal/plan journal). Copy the
folder = complete backup. No cloud, no DB server, no hidden state. Secrets live only
in `.env`. Details in `docs/RUNNING.md`.

## Provenance — what came from where

Nothing here is a fork. This is original code; these are the ideas absorbed (the audit
trail for each is in `docs/ARCHITECTURE.md`):

| Piece | Took the idea from | What's different here |
|---|---|---|
| Event backtester | `hopit-ai/india-trade-cli` (MIT) daily-bar loop | **full Indian cost model** (STT/stamp/GST/SEBI/SLBM/slippage — none of the audited repos had one), next-bar-open fills, ATR trailing + time stops |
| Risk gates, PAPER default, CNC-only | `india-trade-cli` risk_limits / trade_executor | simplified to a tested module + **stale-data dead-man** and kill-switch we added |
| Position sizing | `india-trade-cli` position_sizer (half-Kelly/ATR/correlation) | explicit fixed-fractional formula, unit-tested |
| Switchable AI providers | `Vivek-Bansal04/stock-analyzer` (my own) provider router | strict-JSON contract, per-provider graceful failure, **keyless by default**, architecturally no path to orders |
| Indicator correctness | lesson from stock-analyzer's bugs (Cutler RSI, flat=100) | exact Wilder recursion, tested against Wilder's published table |
| Data adapters design | `BennyThadikaran/NseIndiaApi` + `BseIndiaApi` (GPL) | kept as an **arm's-length dependency** (`pip install NseIndiaApi`), not vendored; own validation + store |
| RRG sector rotation | JdK RRG concept (the Indian repo was unlicensed → not reused) | clean-room weekly implementation + transition detection |
| NAV/mutual-fund layer (Phase 6) | `mftool` (MIT) | will vendor parser+cache only, replace its fake "IRR" with the real tested XIRR already here |
| Strategy defaults | Research: Faber 200-DMA, Moskowitz TSM, Barroso–Santa-Clara vol-scaling, Antonacci dual momentum | all long-only, SMA200-gated, daily |
| Validation | walk-forward practice; overfit ratio | + parameter-plateau gate before anything goes live |

**Excluded on purpose:** `Indian-Stock-Market-API` (no candles, no license), Tapetide
MCP (closed SaaS), the RRG repo's code (unlicensed), all intraday/1-minute/ORB/scalping
templates from every audited repo, ML price-prediction projects.

## Safety posture (design, not promises)

- `TRADING_MODE=paper` default; anything unrecognized fails **closed** to paper.
- Nothing in this codebase can place an order. Execution is manual CNC, your clicks.
- Deterministic risk limits run before strategies and before AI; kill-switch included.
- Backtests include costs + walk-forward + parameter plateau, because past results are
  expectations, not promises.
- Every signal and plan is journaled to `data/plans.sqlite` with a mandatory rationale.

## AI, on your schedule

Zero keys today → deterministic factpack (that's the default and it's complete).
When you buy a model: set its key in `.env` and `AI_PROVIDER_ORDER=openai,anthropic,
gemini,grok,ollama,none` — one line switches; Ollama is free/local. AI output is parsed
strictly, can be rejected, and can never size or place a trade.

## Environment notes

- **Smart App Control on Windows**: only Authenticode-signed binaries load → pandas
  pinned `<3` (3.x wheels unsigned), store uses stdlib sqlite (no duckdb).
- **Corporate/office network**: Yahoo and NSE both TLS-blocked here; run data work on
  a personal/home network. Tests are offline and always runnable.

## Status & roadmap

Implemented: data chain + store, costs, backtester + 6 strategies, RRG, regime,
sizing/limits/XIRR, journal, ETL+Docker, AI router.
Next (see `../build-plan.md`): scanner (Phase 2), delivery-% and PIT/bulk-deal feeds,
broker read-only adapters + holdings reconciliation (Phase 1 tail), tax ledger, Telegram
alerts.
