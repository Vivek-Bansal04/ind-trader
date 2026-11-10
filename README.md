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
| `scan` | Universe shortlist: one evidence row per symbol (trend, RRG quadrant, 6m momentum, delivery %, bulk deals, 20d turnover, compression, vol), ranked; illiquid names (<₹2Cr/day before the signal) dropped; shortlist journaled to `plans.sqlite` |
| `fetch-feeds` | Nightly evidence refresh: delivery-% bhavcopy + bulk deals (NSE; keeps last-known when unreachable) |
| `notify` | Evening briefing (regime + shortlist): sends to Telegram when configured, prints otherwise |
| `bot` | Telegram command interface (long-poll, no public webhook): /status /book /scan /events /ann /fill /pause — chat-id-pinned, and the command registry can't contain order verbs (test-enforced) |
| `pause` / `resume` | Quiet-switch for the daily outbound loop (flag file in DATA_DIR, survives restarts) |
| `reconcile` | Journal vs broker holdings (Fyers read-only token): reports mismatches, never acts on them |
| `fill SYM SIDE QTY PX --costs N` | Record an actual fill into the tax ledger |
| `tax` / `tax --setoff` | FIFO realized P&L + CGT per FY (20%/12.5%, ₹1.25L exemption); `--setoff` applies the statutory loss set-off order + 8-year carry-forward instead of the upper bound |
| `pbacktest [SYMS] --strategy X` | Portfolio-level backtest: shared cash, concurrent positions, regime-scaled exposure |
| `gate SYMBOL --param period=150,200,250` | Walk-forward + parameter-plateau GATE (OOS/IS ratio, IS floor, plateau): exit 2 = strategy fails, net of costs |
| `montecarlo SYMBOL --strategy X` | Bootstrap the costed trade PnLs → p5/p50/p95 outcomes, P(loss), worst-5% drawdown; refuses under 20 trades |
| `weights [SYMS] --method inverse_vol\|hrp` | Risk-balanced weights, capped per symbol; HRP via PyPortfolioOpt with honest auto-fallback |
| `xirr` | Money-weighted return of the actual fills ledger at current marks; refuses on missing marks |
| `riskreport` | Weekly report: regime light + flip/FTD note, book XIRR + concentration, journal counts, universe data health |
| `health` | Data-health gate vs the NIFTY session calendar: rows, gaps, staleness, adj coverage; exit 1 on breaches |
| `fetch-adj` | Store corporate-action factors (yfinance Adj Close/Close); `backtest --adjusted` uses them, warns on RAW fallback |
| `ann` | Announcement triage for HOLDINGS only: RED (fraud/pledge-encroachment/NCLT…) vs AMBER keywords; empty book = empty alert, never a firehose |
| `events [SYM --qty N]` | Results calendar + the plan's event rule: no fresh entry ≤2 sessions before earnings, half size ≤5; unknown date = guard off, stated |
| `review` | Sunday review: benchmark week + regime, book vs executed plans (fills without a plan are flagged), journal hygiene, holding announcements, data health |
| `universe --index "nifty 50" --out data/universe.txt` | Dump live index constituents to a universe file |
| `regime` | Market traffic-light (breadth + index trend) → recommended exposure 100/60/30%, plus the exact levels that would flip the light and the follow-through (FTD) status |
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
| Freshness CI, AI grounding rules, regime flip-note, pre-signal liquidity floor, FTD internals, compression column | second-wave audit (Nov 2026): `claude-trading-skills-india` (MIT) + `nse-stock-analysis` (MIT) | `*_AS_OF` markers with monthly auto-issue workflow; strict grounding SYSTEM prompt + offline contract evals; flip levels name counts, not vibes; turnover floor over the 20 sessions **before** the signal; follow-through is a σ-scaled (1.5×σ, clamped 1–2%) **information line — never a scale change**; tight base = annotation only, never a boost. Deferred on fail-loud grounds: screener.in fundamentals (endpoint unverifiable from this network) and ISIN join keys (broker payload shape unverified) |

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

Implemented: data chain + store, costs, backtester + 6 strategies, portfolio-level backtest,
walk-forward + plateau GATE, Monte Carlo bootstrap, RRG, regime (+ flip levels, + FTD
internals), scanner (+ pre-signal liquidity floor, + compression base, + results-proximity
notes), evidence feeds (delivery/bulk deals), announcement triage (holdings-only RED/AMBER),
results calendar + entry size gate, session calendar + health gate, corporate-action adj
factors, sizing/limits, journal, tax ledger (FIFO + CGT + statutory loss set-off with
8-year carry-forward), book XIRR from the fills ledger, risk-balanced weights (inverse-vol
+ HRP w/ fallback), weekly risk report, Sunday review generator, Fyers read-only
reconciliation, Telegram briefings AND a
chat-pinned command bot (reads + journal only — no order path exists), pause/resume loop
switch, ETL+Docker, AI router (+ grounding rules, contract
evals, falsifier field), freshness CI.
Phases 1–3 of the plan are code-complete; the live-data half (Nifty-500 backfill, bhavcopy
contract test, 10y walk-forward runs) is gated on docs/HOME-VALIDATION.md — this network
blocks NSE/Yahoo. Phase 4 (paper loop, 6–8 weeks) starts from that same checklist.
Next: the laptop session (HOME-VALIDATION §1–§4), then the Phase-4 clock; deferred backlog:
screener.in fundamentals (where the endpoint is verifiable), dashboard UI.
