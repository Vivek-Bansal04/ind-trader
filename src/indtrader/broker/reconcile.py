"""Journal-vs-broker reconciliation: where intent and reality diverged.

Broker holdings are ground truth for WHAT IS HELD; the plan journal is ground truth
for WHAT WE INTENDED. Every divergence is reported to a human — this module never
"splits the difference" and never trades to fix anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from indtrader.broker.fyers import Holding


@dataclass
class ReconcileReport:
    missing_at_broker: list[str] = field(default_factory=list)     # journal says held, broker says no
    unjournalled: list[str] = field(default_factory=list)          # broker holds, journal has no plan
    qty_mismatch: list[tuple[str, int, int]] = field(default_factory=list)  # (symbol, journal, broker)
    broker_total: int = 0
    journal_total: int = 0

    @property
    def has_issues(self) -> bool:
        return bool(self.missing_at_broker or self.unjournalled or self.qty_mismatch)

    def lines(self) -> list[str]:
        out = [f"reconcile: broker {self.broker_total} symbols vs journal {self.journal_total}"]
        for s in self.missing_at_broker:
            out.append(f"  MISSING AT BROKER: {s} — journal marks it executed, broker shows nothing")
        for s in self.unjournalled:
            out.append(f"  UNJOURNALLED: {s} — at broker, no executed plan (manual buy? journal it)")
        for sym, jq, bq in self.qty_mismatch:
            out.append(f"  QTY MISMATCH: {sym} journal {jq} vs broker {bq}")
        if not self.has_issues:
            out.append("  all executed plans match broker holdings")
        return out


def reconcile(journal: dict[str, int], holdings: list[Holding]) -> ReconcileReport:
    broker = {h.symbol: h.qty for h in holdings}
    rep = ReconcileReport(broker_total=len(broker), journal_total=len(journal))
    for sym, jq in sorted(journal.items()):
        if sym not in broker:
            rep.missing_at_broker.append(sym)
        elif broker[sym] != jq:
            rep.qty_mismatch.append((sym, jq, broker[sym]))
    for sym in sorted(broker):
        if sym not in journal:
            rep.unjournalled.append(sym)
    return rep
