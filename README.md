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
| `fetch RELIANCE` / `fetch-universe [--skip-fresh]` | Download daily candles into the local store (NSE archive via NseIndiaApi, Yahoo fallback); `--skip-fresh` skips symbols already complete-and-current (the nightly loop uses it by default, so re-runs cost milliseconds, not 10 years × 500 symbols) |
| `backtest SYM --strategy X --costs` | Daily-bar backtest with real Indian delivery costs and next-bar-open fills |
| `rrg SYM SYM ... --bench NIFTY` | Clean-room Relative Rotation Graph: quadrant + quadrant transitions |
| `scan` | Universe shortlist: one evidence row per symbol (trend, RRG quadrant, 6m momentum, delivery %, bulk deals, 20d turnover, compression, vol), ranked; illiquid names (<₹2Cr/day before the signal) dropped; shortlist journaled to `plans.sqlite` |
| `fetch-feeds` | Nightly evidence refresh: delivery-% bhavcopy + bulk deals (NSE; keeps last-known when unreachable) |
| `notify` | Evening briefing (regime + shortlist): sends to Telegram when configured, prints otherwise |
| `bot` | Telegram command interface (long-poll, no public webhook): /status /book /scan /events /ann /plan /plans /fill /pause — chat-id-pinned, and the command registry can't contain order verbs (test-enforced) |
| `pause` / `resume` | Quiet-switch for the daily outbound loop (flag file in DATA_DIR, survives restarts) |
| `reconcile` | Journal vs broker holdings (Fyers read-only token): reports mismatches, never acts on them |
| `fill SYM SIDE QTY PX --costs N` | Record an actual fill into the tax ledger |
| `tax` / `tax --setoff` | FIFO realized P&L + CGT per FY (20%/12.5%, ₹1.25L exemption); `--setoff` applies the statutory loss set-off order + 8-year carry-forward instead of the upper bound |
| `pbacktest [SYMS] --strategy X` | Portfolio-level backtest: shared cash, concurrent positions, regime-scaled exposure |
| `gate SYMBOL --param period=150,200,250` | Walk-forward + parameter-plateau GATE (OOS/IS ratio, IS floor, plateau): exit 2 = strategy fails, net of costs |
| `montecarlo SYMBOL --strategy X` | Bootstrap the costed trade PnLs → p5/p50/p95 outcomes, P(loss), worst-5% drawdown; refuses under 20 trades |
| `weights [SYMS] --method inverse_vol\|hrp` | Risk-balanced weights, capped per symbol; HRP via PyPortfolioOpt with honest auto-fallback |
| `xirr` | Money-weighted return of the actual fills ledger at current marks; refuses on missing marks |
| `mf-navs` | Store latest MF NAVs from AMFI's NAVAll.txt (live-verified header contract; 0-silent-misparse by design) — works from any network that reaches amfiindia.com, office included |
| `mf [text]` | No arg: MF slice of the book at stored NAVs; with text: scheme search by name |
| `fill-mf CODE SIDE UNITS NAV [--date]` | Record one SIP instalment/redemption — fractional units, NAV-priced (ledger rows carry the MF: prefix) |
| `sips` | SIP aggregation (units, invested, value, gain%) + XIRR of the MF slice; refuses on missing NAVs |
| `action SYM KIND [--qty --cash --tds --date]` | Record a corporate action EXACTLY as the statement showed it (dividend/bonus/split/redemption) — never inferred from price jumps |
| `actions` | List recorded corporate actions; effects: dividend → XIRR flow + per-FY income (gross/TDS/net) in `tax`, bonus → zero-cost lot, split → FIFO basis rescale, redemption → synthesized SELL |
| `rebalance --file targets.json` | Monthly rebalance proposals vs target weights with the tax lens on every sell; unknown marks size 0, never guessed; proposals only |
| `riskreport` | Weekly report: regime light + flip/FTD note, book XIRR + concentration, journal counts, universe data health |
| `health` | Data-health gate vs the NIFTY session calendar: rows, gaps, staleness, adj coverage; exit 1 on breaches. Plus the split-gap check: every ±35% raw move must have a matching factor step — `adj_coverage=100%` can no longer hide an unadjusted split cliff (round-4 HDFCBANK lie) |
| `contract-test` | Phase-1 acceptance: store closes vs NSE's own bhavcopy (`TckrSymb`/`ClsPric` contract, captured live — never guessed); names every mismatch, exit 1 on any |
| `holidays [--refresh-calendar]` | NSE CBM trading-holiday calendar + cross-check that no holiday has a NIFTY bar. `--refresh-calendar` also stores the exchange holiday master (offline drift ledger: `disputed_dates` = exchange-holiday dates that have bars, re-computable any time) |
| `fetch-adj` | Store corporate-action factors (yfinance Adj Close/Close); `backtest --adjusted` uses them, warns on RAW fallback |
| `ann` | Announcement triage for HOLDINGS only: RED (fraud/pledge-encroachment/NCLT…) vs AMBER keywords; empty book = empty alert, never a firehose |
| `events [SYM --qty N]` | Results calendar + the plan's event rule: no fresh entry ≤2 sessions before earnings, half size ≤5; unknown date = guard off, stated |
| `review` | Sunday review: benchmark week + regime, book vs executed plans (fills without a plan are flagged), journal hygiene, holding announcements, data health |
| `plan SYM BUY --qty --entry --stop --target --reason "..."` | Written plan (Phase-4 door): rationale mandatory, stop<entry<target enforced at creation |
| `plans [--all]` / `plan-mark ID STATUS` | Plan journal: open-first list, manual executed/cancelled/stale close-outs |
| `fill ... --plan ID` | Fill that executes an OPEN plan: ledger note keeps `plan#N` trace, plan flips to executed, per-fill slippage vs the planned entry printed; unknown/closed plans refused |
| `attribution [--index]` | Realized P&L by setup × index-trend-at-entry; fills without a plan show as `unplanned`, missing index = `?` — never hidden |
| `compare SYM [--strategies a,b] [--costs]` | A/B every strategy on the same window; default gross, `--costs` for full STT/slippage; prints the buy&hold bar to beat |
| `harvest [--asof]` | Pre-March candidates from open FIFO lots × marks × FY realized LTCG: loss-harvest / free basis-reset (inside exemption) / taxable reset priced at 12.5%; unmarked symbols skipped and NAMED. Computation, not advice |
| `decay SYM --strategy X` | Paper trades vs the strategy's own bootstrap band (p5/p95 + rolling windows); thin baseline refuses to judge; a breach prints REVIEW — a human decides |
| `import-csv FILE [--format f] [--map ...]` | Broker tradebook seeding: contract-first parse, bad row aborts naming the row, re-runs dedup, NOTHING writes without `--apply` |
| `shareholding [SYM] [--fetch\|--set ...]` | QoQ ownership deltas (promoter −2% / FII exit / pledge ↑). `--fetch` sweeps every method/arg-form and records the round-4-CONFIRMED quarterly summary shape from whichever payload carries it (detail views are reported, never parsed; promoter/public only — FII/DII/pledge aren't in that payload and stay NULL, never zeroed) |
| `coractions SYM [--from-raw\|--set\|--gap-list]` | The split-factor cure: probe every method/arg-form with a shape inventory; `--from-raw` replays the confirmed-shape parser offline; `--set DATE\|KIND\|RATIO` records one human-confirmed event (ratio = price multiplier) when NSE's text states none; `--gap-list` batch-probes every symbol `health` names. Ratios only from stated numbers or humans — unresolved events are NAMED, never guessed |
| `news [SYMS] [--no-fetch] [--days]` | External news (Google News RSS, no key) for holdings + MARKET — triaged with the SAME RED/AMBER vocabulary as `ann`, so a headline can't invent a new category. Transport is `data.http` (curl_cffi impersonation where present: the round-5 laptop 403'd plain urllib) and the fetch line names which transport ran. A dead feed prints as an outage, never as a quiet evening; tests are offline |
| `backup [--keep N]` / `restore KIND FILE` | Sqlite online backup (safe mid-ETL) + `PRAGMA integrity_check` on every copy; restore refuses corrupt backups, keeps `.pre-restore` |
| `dashboard [--port 8765]` | Read-only one-page web UI on 127.0.0.1: banner (NIFTY + staleness + PAUSED), book vs plans, open plans, last scan, journal gaps, health strip. Stdlib only, zero write paths |
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
notes, + dead-man staleness gate vs today), evidence feeds (delivery/bulk deals),
announcement triage (holdings-only RED/AMBER), results calendar + entry size gate, session
calendar + health gate, bhavcopy contract test + holiday calendar (both live-confirmed),
corporate-action adj factors, sizing/limits, plan journal with a written-plan front door
(`plan`/`plans`/`plan-mark`, `fill --plan` link), realized-vs-plan attribution (setup ×
trend-at-entry), strategy A/B `compare`, tax ledger (FIFO + CGT + statutory loss set-off
with 8-year carry-forward), tax-loss harvest candidates, paper-vs-backtest decay monitor,
book XIRR, risk-balanced weights (inverse-vol + HRP w/ fallback), weekly risk report,
Sunday review generator, shareholding-pattern deltas (live side probe-only), broker CSV
import (contract-first, `--apply`-gated), sqlite backup/restore with integrity checks,
read-only web dashboard (stdlib, loopback), Fyers read-only reconciliation, Telegram
briefings AND a
chat-pinned command bot (reads + journal only — no order path exists), pause/resume loop
switch, ETL+Docker, AI router (+ grounding rules, contract
evals, falsifier field), freshness CI.
Phases 1–3 of the plan are code-complete; the live-data half (Nifty-500 backfill finish,
10y walk-forward runs on the laptop, shareholding payload probe) runs on the <PERSON>
per ROUND3-TODO.md — this network blocks NSE/Yahoo. Phase 4 (paper loop, 6–8 weeks)
starts from that same checklist; its discipline tooling (plans, attribution, decay,
dead-man) is all in. Phase 6 (investing leg) is feature-complete offline and live-verified where possible:
MF NAV layer (AMFI is NOT blocked — 14,445 NAVs stored, contract-probe green, 2026-11-10),
SIP ledger with fractional units, dividend/corporate-action ledger (cost-basis-correct
FIFO treatment, dividend income in the tax sheet, redemption sells synthesized),
rebalance proposals with the tax lens, MF slice + CA income surfaced in review and
riskreport. Phase 5 stays gated on
Phase 4 passing, per plan; its buildable surface already exists (read-only reconcile,
journal, sizing).
Next: the laptop session (ROUND3-TODO.md), then the Phase-4 clock. Deferred backlog —
each item gets built only if it earns its place, otherwise dropped:
- screener.in fundamentals (where the endpoint is verifiable).
- NSE's official MCP server (nseindia.com/nse-mcp, live since ~Jun 2026, no key).
  Probed 2026-11-11: `https://mcp.nseindia.in/bhavcopy/cm/mcp` answers MCP
  initialize + tools/list (21 tools) **even from the office network** that 403s
  every other NSE endpoint. Not a store replacement (5y window, unadjusted
  prices, plain bhavcopy = what `contract-test` already validates us against).
  Candidates if ever adopted, all read-only: `get_index_valuation` (index PE/PB/
  yield + percentile — the §C "index valuation" item), `get_market_mood` (India
  VIX for the regime light), `get_corporate_actions` (official NSE adjustmentFactor
  as a second source to cross-check our yfinance `fetch-adj` factors). Terms:
  educational use only, no trading/commercial. v1.0.0, six months old — any
  wiring must refuse loudly on shape drift like everything else here.
