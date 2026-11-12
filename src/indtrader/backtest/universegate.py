"""Universe-level walk-forward gate with split-gap awareness (Phase-5 machinery).

A gate run over a symbol with an UNEXPLAINED split cliff measures a fabricated
-50% move: every fold inherits the fiction. So before any fold runs, this harness
consults the SAME gap check the health gate and the dashboard use — one shared
implementation, `research/splitgaps.py` — and EXCLUDES such symbols, naming each
and printing the excluded count. A batch that silently mixes poisoned rows into
a clean-looking pass/fail tally is the failure this module exists to prevent.

Statuses are exhaustive and honest: PASS / FAIL / EXCLUDED (pending factors) /
TOO_SHORT (can't say yet — not a fail) / NODATA (not fetched — unknown, not a
fail either). `--include-gappy` lets a human run one anyway; the warning still
prints, exclusion is the default, never the silent choice.

Adjusted vs raw: the batch loads ADJUSTED series where stored factors exist —
an explained cliff should not poison folds either. The single-symbol `gate`
command keeps its existing raw behavior (documented in docs/RULES.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from indtrader.backtest.costs import DeliveryCosts
from indtrader.backtest.walkforward import GateReport, walk_forward_gate
from indtrader.research.splitgaps import symbol_split_gaps


@dataclass
class SymbolGate:
    symbol: str
    status: str                      # PASS | FAIL | EXCLUDED | TOO_SHORT | NODATA
    detail: str = ""                 # why (gaps, bar count) — never empty on non-PASS
    report: GateReport | None = None


@dataclass
class UniverseGateReport:
    strategy: str
    metric: str
    rows: list[SymbolGate] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def summary_line(self) -> str:
        c = self.counts()
        parts = [f"{c.get('PASS', 0)} pass", f"{c.get('FAIL', 0)} fail"]
        if c.get("EXCLUDED"):
            parts.append(f"{c['EXCLUDED']} excluded pending factors")
        if c.get("TOO_SHORT"):
            parts.append(f"{c['TOO_SHORT']} too short")
        if c.get("NODATA"):
            parts.append(f"{c['NODATA']} no data")
        return f"gate-universe [{self.strategy}/{self.metric}]: " + " | ".join(parts)


def run_universe_gate(store, symbols: list[str], cls, grid: dict[str, list],
                      costs: DeliveryCosts | None = None, metric: str = "sharpe",
                      include_gappy: bool = False, warmup: int = 400,
                      test_days: int = 252, min_bars: int | None = None,
                      progress: Callable[[str], None] | None = None) -> UniverseGateReport:
    """One gate per symbol, gaps-aware. `symbols` come from the caller (a universe
    file) — this never invents its own list."""
    rep = UniverseGateReport(strategy=cls.name, metric=metric)
    need = warmup + 2 * test_days if min_bars is None else min_bars
    for sym in symbols:
        try:
            df = store.load(sym, adjusted=True)
        except Exception as e:  # a broken series is named, not swallowed
            rep.rows.append(SymbolGate(sym, "NODATA", f"load failed: {type(e).__name__}: {e}"))
            continue
        if df is None or df.empty:
            rep.rows.append(SymbolGate(sym, "NODATA", "not in store — run fetch-universe"))
            continue
        gaps = symbol_split_gaps(store, sym, store.load(sym, adjusted=False))
        if gaps and not include_gappy:
            rep.rows.append(SymbolGate(sym, "EXCLUDED",
                                       f"{len(gaps)} unexplained cliff(s): " + "; ".join(gaps[:2])))
            continue
        if gaps and include_gappy:
            detail = f"ran DESPITE {len(gaps)} cliff(s): " + "; ".join(gaps[:2]) + " — "
        else:
            detail = ""
        if len(df) < need:
            rep.rows.append(SymbolGate(sym, "TOO_SHORT",
                                       f"{detail}{len(df)} bars, gate needs {need}"))
            continue
        gate = walk_forward_gate(df, cls, grid, costs=costs, metric=metric,
                                 warmup=warmup, test_days=test_days)
        if progress:
            progress(f"{sym}: {'PASS' if gate.passed else 'FAIL'}"
                     + (f" — {gate.reasons[0]}" if not gate.passed and gate.reasons else ""))
        first = gate.reasons[0] if (not gate.passed and gate.reasons) else ""
        rep.rows.append(SymbolGate(sym, "PASS" if gate.passed else "FAIL", detail + first, gate))
    return rep
