"""Tax-loss harvesting candidates (plan §B: pre-March "harvest candidates" list).

Pure ledger math on data already stored — open FIFO lots x current marks x the FY's
realized P&L. Three kinds of candidate, each labeled with the RULE it serves:

- loss lots (held < 12 months, unrealized loss): selling books an STCG loss now,
  worth up to STCG_RATE against tax otherwise due — this is the set-off engine's
  fuel (see tax_summary_with_setoff).
- loss lots held >= 12 months: same but LTCG losses (set-off against LTCG only).
- gain lots held >= 12 months beyond the remaining LTCG exemption: "harvest" =
  sell-and-rebuy resets the cost basis to market; the price of the reset is the tax
  on the gain above the exemption. Gains still inside the exemption are FREE resets.

Marks with no price are SKIPPED AND NAMED — a candidate list computed on missing
marks would be guesswork about guesswork. Computation only, not advice."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from indtrader.portfolio.tax import (LTCG_EXEMPTION, LTCG_RATE, STCG_RATE,
                                     fy_of, months_between, open_lots)


@dataclass(frozen=True)
class Candidate:
    symbol: str
    lot_dt: date
    qty: float
    cost: float           # per unit
    mark: float
    unrealized: float     # net of pro-rated buy costs
    bucket: str           # STCG | LTCG (by holding period as of asof)
    kind: str             # loss-harvest | free-basis-reset | taxable-basis-reset
    tax_effect: float     # positive = tax cost to harvest; negative = tax saved


def harvest_candidates(entries: list, marks: dict[str, float],
                       matches: list | None = None, asof: date | None = None) -> dict:
    asof = asof or date.today()
    fy = fy_of(asof)
    realized_ltcg = sum(m.net for m in (matches or [])
                        if m.bucket == "LTCG" and fy_of(m.sell_dt) == fy)
    exemption_left = max(0.0, LTCG_EXEMPTION - max(0.0, realized_ltcg))

    cands, skipped = [], []
    for lot in open_lots(entries):
        px = marks.get(lot.symbol)
        if px is None:
            skipped.append(lot.symbol)
            continue
        buy_cost = lot.costs * (lot.qty / lot.fill_qty) if lot.fill_qty else 0.0
        gain = (px - lot.price) * lot.qty - buy_cost
        bucket = "LTCG" if months_between(lot.dt, asof) >= 12 else "STCG"
        if gain < 0:
            saved = round((STCG_RATE if bucket == "STCG" else LTCG_RATE) * -gain, 2)
            cands.append(Candidate(lot.symbol, lot.dt, lot.qty, lot.price, px,
                                   round(gain, 2), bucket, "loss-harvest", -saved))
        elif bucket == "LTCG":
            taxable_part = max(0.0, gain - exemption_left)
            if taxable_part > 0:
                cands.append(Candidate(lot.symbol, lot.dt, lot.qty, lot.price, px,
                                       round(gain, 2), bucket, "taxable-basis-reset",
                                       round(taxable_part * LTCG_RATE, 2)))
            else:
                cands.append(Candidate(lot.symbol, lot.dt, lot.qty, lot.price, px,
                                       round(gain, 2), bucket, "free-basis-reset", 0.0))
    cands.sort(key=lambda c: (c.tax_effect, -abs(c.unrealized)))
    return {"asof": str(asof), "financial_year": fy,
            "realized_ltcg_this_fy": round(realized_ltcg, 2),
            "exemption_left": round(exemption_left, 2),
            "candidates": [c.__dict__ for c in cands],
            "skipped_no_mark": sorted(set(skipped))}


def lines(rep: dict) -> list[str]:
    out = [f"Harvest candidates {rep['asof']} ({rep['financial_year']}) — realized "
           f"LTCG {rep['realized_ltcg_this_fy']:,.0f}, exemption left {rep['exemption_left']:,.0f}"]
    if not rep["candidates"]:
        out.append("  none (no open lot qualifies on current marks)")
    for c in rep["candidates"]:
        out.append(f"- {c['symbol']} lot {c['lot_dt']} qty {c['qty']:g} "
                   f"cost {c['cost']:,.1f} mark {c['mark']:,.1f} -> {c['unrealized']:+,.0f} "
                   f"[{c['bucket']} {c['kind']}]"
                   + (f" tax saved ~{-c['tax_effect']:,.0f}" if c['tax_effect'] < 0
                      else f" tax cost ~{c['tax_effect']:,.0f}" if c['tax_effect'] > 0
                      else "  [free — inside exemption]"))
    if rep["skipped_no_mark"]:
        out.append(f"skipped, no mark: {', '.join(rep['skipped_no_mark'])}")
    out.append("computation only — sell/rebuy mechanics and limits: verify with your CA.")
    return out
