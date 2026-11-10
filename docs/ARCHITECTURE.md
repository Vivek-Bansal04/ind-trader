# Architecture & provenance

```
ETL (providers) -> BarStore (sqlite, validated) -> strategies(signals on closed bars)
    -> Backtester (next-bar-open fills, Indian costs) / Scanner+RRG / Regime
    -> Sizing (risk-based + corr penalty + caps) -> RiskManager (hard limits, kill-switch)
    -> PlanLog (audit journal) -> human-confirmed execution (paper today, CNC broker later)
                                            ^ AI router annotates; never decides, never trades
```

## Non-negotiable design rules

1. Daily/weekly bars only. No code path consumes sub-daily data.
2. Signals on close of bar i; fills modelled/ordered at open of bar i+1 (no lookahead).
3. Long-only, delivery (CNC). No product field to flip.
4. Every fill pays real Indian costs; every read/write of candles is OHLC-validated.
5. AI is an optional annotation of the deterministic factpack. Router order is env-driven;
   the pipeline runs identically with zero keys. LLM output is parsed strictly and can be
   rejected; there is no path from model output to broker.
6. Risk gates are deterministic and run before strategies/LLM (kill-switch, loss caps,
   per-symbol caps, stale-data dead-man).

## Where each idea came from (audited Nov 2026)

| Piece | Source of the idea | What we deliberately changed |
|---|---|---|
| Indicators | stock-analyzer was subtly wrong (Cutler RSI, flat=100 bug, 30-bar history) | exact Wilder recursion, golden-value tests, ≥10y windows |
| Backtester core | india-trade-cli `backtest.py` (daily-bar event loop) | added full Indian cost model, next-bar-open fills, ATR trailing stop, time stop |
| Cost model | none of the audited repos had one | new: STT/stamp/exchange/SEBI/GST/SLBM/slippage as one config object |
| Strategies | literature (below) + engine registry | all gated by SMA200 trend; long-only; no intraday templates at all |
| Risk/sizing | india-trade-cli position_sizer (half-Kelly/ATR/corr) + classic fixed-fractional | simplified to explicit, tested functions; caps duplicated at limit layer |
| Kill-switch / dead-man | india-trade-cli risk_limits + our own addition for stale data | plus "no fresh bars, no orders" |
| RRG | concept from Relative Rotation Graphs (JdK); the Indian repo was unlicensed | clean-room reimplementation, weekly bars, transition detection |
| AI layer | stock-analyzer provider-router pattern | strict-JSON contract, per-provider graceful failure, keyless default, zero order path |
| Data adapters | NseIndiaApi/BseIndiaApi (BennyThadikaran) design: throttle, cookie store, retry | vendored as separate adapters in Phase 1; protocol defined here already |
| Store | mftool cache lessons + engine's JSON-cache weakness | sqlite of record, idempotent upserts, validate-on-read |
| Walk-forward / plateau | standard WF validation; CPCV upgrade planned | overfit ratio + parameter-plateau gate before any strategy goes live |

## Research encoded as defaults

- **Faber (2007)** and **Smith & N. (2013)**: simple ~200-day MA trend rules beat short,
  complex ones; monthly decisions ≈ daily decisions with less whipsaw → SMA200 gates,
  end-of-day decisions, no intraday.
- **Moskowitz, Ooi & Pedersen (2012)**: time-series momentum with volatility scaling →
  TSMomentum strategy + ATR-normalized sizing.
- **Barroso & Santa-Clara (2015)**: vol-scaling tames momentum crashes → regime throttle
  (ON/NEUTRAL/OFF exposure scaling).
- **Antonacci (2012)**: dual momentum (absolute + relative) → DualMomentum strategy;
  relative leg belongs to the scanner's RRG/universe ranking.
- **Wilder (1978)**: RSI/ATR definitions — implemented exactly, tested against his tables.
- Stops: index-level studies (Smith & N.) show stops add little to trend systems, but
  single stocks gap — stops kept for capital preservation, with/without comparison expected
  in every research run.

## Known limits (honest list)

- yfinance is the only live provider today (works on normal networks; blocked on the
  corp laptop's TLS-intercepting proxy). NseIndiaApi + broker adapters are the Phase-1 work.
- Single-symbol backtests; portfolio-level concurrent simulation comes with the scanner.
- No fundamentals, news, PIT/bulk-deal or shareholding feeds yet (Phase 2-3, feeds exist).
- XIRR is implemented and tested but not yet wired to a ledger (Phase 6 investing leg).
