"""Realized-P&L tax ledger: FIFO matching + Indian equity CGT (Finance Act 2024 rates).

Rules encoded (equity, no-STT case excluded — every delivery trade pays STT here):
- holding < 12 months -> STCG @ 20%; >= 12 months -> LTCG @ 12.5%
- LTCG exemption Rs 1.25 lakh per financial year (April-March)
- FIFO matching (the accepted method for equities)
- sell-side costs reduce the gain, buy-side costs increase the cost basis
Deliberately NOT implemented (yet, documented): loss set-off order and 8-year
carry-forward — losses are reported, not applied, so the number shown is the
upper bound of tax, never an under-estimate. This is computation, not advice.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date

MONTHS_IN_A_YEAR = 12


@dataclass(frozen=True)
class LedgerEntry:
    dt: date
    symbol: str
    side: str          # BUY | SELL
    qty: int
    price: float
    costs: float = 0.0  # all-in transaction costs for this fill


@dataclass(frozen=True)
class RealizedMatch:
    symbol: str
    buy_dt: date
    sell_dt: date
    qty: int
    gross: float        # (sell_px - buy_px) * qty
    costs: float        # pro-rated buy+sell costs
    bucket: str         # STCG | LTCG

    @property
    def net(self) -> float:
        return self.gross - self.costs


def months_between(d1: date, d2: date) -> int:
    return (d2.year - d1.year) * 12 + (d2.month - d1.month) - (1 if d2.day < d1.day else 0)


def fy_of(d: date) -> str:
    """Indian financial year label: Apr 2024 - Mar 2025 -> 'FY2024-25'."""
    y = d.year if d.month >= 4 else d.year - 1
    return f"FY{y}-{str(y + 1)[-2:]}"


def realize(entries: list[LedgerEntry]) -> list[RealizedMatch]:
    """FIFO match fills into realized gains/losses. Raises on a sell exceeding holdings."""
    books: dict[str, deque[list]] = {}
    out: list[RealizedMatch] = []
    for e in sorted(entries, key=lambda x: (x.dt, x.side == "BUY")):
        book = books.setdefault(e.symbol.upper(), deque())
        if e.side.upper() == "BUY":
            book.append([e.dt, e.qty, e.price, e.costs])
            continue
        remaining_qty, remaining_costs = e.qty, e.costs
        while remaining_qty > 0:
            if not book:
                raise ValueError(f"{e.symbol}: sell {e.qty} on {e.dt} exceeds holdings (FIFO)")
            lot = book[0]
            take = min(lot[1], remaining_qty)
            buy_cost = lot[3] * (take / lot[1]) if lot[1] else 0.0
            sell_cost = remaining_costs * (take / e.qty) if e.qty else 0.0
            gross = (e.price - lot[2]) * take
            bucket = "LTCG" if months_between(lot[0], e.dt) >= MONTHS_IN_A_YEAR else "STCG"
            out.append(RealizedMatch(e.symbol.upper(), lot[0], e.dt, take, gross,
                                     round(buy_cost + sell_cost, 2), bucket))
            lot[1] -= take
            remaining_qty -= take
            if lot[1] == 0:
                book.popleft()
    return out


LTCG_EXEMPTION = 125_000.0
STCG_RATE = 0.20
LTCG_RATE = 0.125


@dataclass(frozen=True)
class FyTax:
    financial_year: str
    stcg_net: float
    ltcg_net: float
    ltcg_taxable: float
    tax_stcg: float
    tax_ltcg: float

    @property
    def tax_total(self) -> float:
        return round(self.tax_stcg + self.tax_ltcg, 2)

    def lines(self) -> list[str]:
        return [
            f"{self.financial_year}: STCG {self.stcg_net:,.0f} | LTCG {self.ltcg_net:,.0f}"
            f" (taxable {self.ltcg_taxable:,.0f}, exempt up to {LTCG_EXEMPTION:,.0f})",
            f"   tax @20%/12.5% = {self.tax_stcg:,.0f} + {self.tax_ltcg:,.0f} = {self.tax_total:,.0f}"
            "  [losses reported, not set off]",
        ]


def tax_summary(matches: list[RealizedMatch]) -> list[FyTax]:
    if not matches:
        return []
    per_fy: dict[str, dict[str, float]] = {}
    for m in matches:
        d = per_fy.setdefault(fy_of(m.sell_dt), {"stcg": 0.0, "ltcg": 0.0})
        d["stcg" if m.bucket == "STCG" else "ltcg"] += m.net
    out = []
    for fy in sorted(per_fy):
        stcg, ltcg = per_fy[fy]["stcg"], per_fy[fy]["ltcg"]
        ltcg_taxable = max(0.0, ltcg - LTCG_EXEMPTION)  # exemption only helps positive LTCG
        out.append(
            FyTax(fy, round(stcg, 2), round(ltcg, 2), round(ltcg_taxable, 2),
                  round(max(0.0, stcg) * STCG_RATE, 2), round(ltcg_taxable * LTCG_RATE, 2))
        )
    return out
