"""Realized-P&L tax ledger: FIFO matching + Indian equity CGT (Finance Act 2024 rates).

Rules encoded (equity, no-STT case excluded — every delivery trade pays STT here):
- holding < 12 months -> STCG @ 20%; >= 12 months -> LTCG @ 12.5%
- LTCG exemption Rs 1.25 lakh per financial year (April-March)
- FIFO matching (the accepted method for equities)
- sell-side costs reduce the gain, buy-side costs increase the cost basis
Deliberately excluded: nothing on losses — tax_summary() reports the no-set-off upper
bound, tax_summary_with_setoff() does the real chronological set-off with 8-year
carry-forward (documented simplifications, see there). Computation, not advice.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date

MONTHS_IN_A_YEAR = 12
# Finance Act 2024 (Jul-2024) slabs encoded below; freshness CI tracks this date.
TAX_SLABS_AS_OF = "2026-11-01"


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
class OpenLot:
    """A still-held FIFO lot: acquisition date, remaining qty, per-unit cost and the
    pro-ration anchor for its buy-side costs (the original fill qty)."""
    symbol: str
    dt: date
    qty: float
    price: float
    costs: float
    fill_qty: float


def open_lots(entries: list[LedgerEntry]) -> list[OpenLot]:
    """FIFO-consume the sells and return what is LEFT — the unrealized side of the
    ledger. Same traversal as realize(), so harvested candidates and realized P&L
    can never disagree about which lots exist."""
    books: dict[str, deque[list]] = {}
    for e in sorted(entries, key=lambda x: (x.dt, x.side == "BUY")):
        book = books.setdefault(e.symbol.upper(), deque())
        if e.side.upper() == "BUY":
            book.append([e.dt, e.qty, e.price, e.costs, e.qty])
            continue
        remaining = e.qty
        while remaining > 0:
            if not book:
                raise ValueError(f"{e.symbol}: sell {e.qty} on {e.dt} exceeds holdings (FIFO)")
            lot = book[0]
            take = min(lot[1], remaining)
            lot[1] -= take
            remaining -= take
            if lot[1] == 0:
                book.popleft()
    out = []
    for sym, book in books.items():
        for dt, qty, price, costs, fill_qty in book:
            if qty > 0:
                out.append(OpenLot(sym, dt, qty, price, costs, fill_qty))
    return sorted(out, key=lambda l: (l.symbol, l.dt))


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

    def lines(self, dividend: dict | None = None) -> list[str]:
        out = [
            f"{self.financial_year}: STCG {self.stcg_net:,.0f} | LTCG {self.ltcg_net:,.0f}"
            f" (taxable {self.ltcg_taxable:,.0f}, exempt up to {LTCG_EXEMPTION:,.0f})",
            f"   tax @20%/12.5% = {self.tax_stcg:,.0f} + {self.tax_ltcg:,.0f} = {self.tax_total:,.0f}"
            "  [losses reported, not set off]",
        ]
        if dividend:
            out.append(f"   dividend income: gross {dividend['gross']:,.0f} - TDS {dividend['tds']:,.0f}"
                       f" = net {dividend['net']:,.0f}  [slab-rate income; reported, not computed]")
        return out


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


# --- loss set-off + 8-year carry-forward (the sequence ITR return-preparation uses) ---
# Documented simplifications: no inter-bucket detail beyond the statutory rule that
# STCG(111A) losses can absorb LTCG(112) but NOT vice versa; brought-forward losses are
# applied after intrayear set-off; carry ages by calendar years but the FY1->FY2 expiry
# boundary is approximated by processing year. This is computation, not advice — verify
# against your CA.

SET_OFF_YEARS = 8


@dataclass(frozen=True)
class FySetOff:
    financial_year: str
    stcg_net: float            # raw net STCG this year (sign kept)
    ltcg_net: float
    used_brought_stcl: float   # carried-in STCG losses applied
    used_brought_ltlg: float
    taxable_stcg: float
    taxable_ltcg: float        # after set-off and Rs 1.25L exemption
    exemption_used: float
    tax_total: float
    carry_stcg_out: float      # losses into next FY (each year ages them; gone after 8)
    carry_ltcg_out: float
    note: str = ""


def tax_summary_with_setoff(matches: list[RealizedMatch]) -> list[FySetOff]:
    """Chronological FY walk with loss memory. Returns one row per FY that has P&L,
    plus nothing when there are no matches — never a zero-filled history."""
    if not matches:
        return []
    per_fy: dict[str, dict[str, float]] = {}
    for m in matches:
        d = per_fy.setdefault(fy_of(m.sell_dt), {"stcg": 0.0, "ltcg": 0.0})
        d["stcg" if m.bucket == "STCG" else "ltcg"] += m.net
    carry_s: list[list] = []  # [age_years, amount>0] — STCG losses, set-off-able vs STCG then LTCG
    carry_l: list[list] = []  # LTCG losses, set-off-able vs LTCG only
    out: list[FySetOff] = []
    prev_start: int | None = None
    for fy in sorted(per_fy):
        # 0. age carry by CALENDAR years since the last FY with P&L (gap years age too —
        #    a loss must not survive 8+ years just because the book was quiet), then drop
        #    what has already reached the limit before this year can use it
        start = int(fy[2:6])
        expired_s = expired_l = 0.0
        if prev_start is not None:
            for lot in carry_s + carry_l:
                lot[0] += max(0, start - prev_start - 1)
        expired_s += sum(lot[1] for lot in carry_s if lot[0] >= SET_OFF_YEARS)
        expired_l += sum(lot[1] for lot in carry_l if lot[0] >= SET_OFF_YEARS)
        carry_s = [lot for lot in carry_s if lot[0] < SET_OFF_YEARS]
        carry_l = [lot for lot in carry_l if lot[0] < SET_OFF_YEARS]
        prev_start = start
        s, l = per_fy[fy]["stcg"], per_fy[fy]["ltcg"]

        # 1. intrayear: STCG loss absorbs LTCG gain (statutory), not vice versa
        used_stcl_intrayear = 0.0
        if s < 0 and l > 0:
            used_stcl_intrayear = min(-s, l)
            s += used_stcl_intrayear
            l -= used_stcl_intrayear
        # 2. current STCG gains absorb brought STCG losses (oldest first)
        used_b_s = 0.0
        if s > 0 and carry_s:
            for lot in carry_s:
                take = min(lot[1], s)
                lot[1] -= take
                s -= take
                used_b_s += take
                if lot[1] <= 1e-9:
                    lot[1] = 0.0
            carry_s = [lot for lot in carry_s if lot[1] > 1e-9]
        # 3. brought-forward STCL may still absorb current LTCG (statutory 111A->112);
        #    the intrayear pair was already handled in step 1
        if l > 0 and carry_s:
            for lot in carry_s:
                take = min(lot[1], l)
                lot[1] -= take
                l -= take
                used_b_s += take
            carry_s = [lot for lot in carry_s if lot[1] > 1e-9]
        # 4. LTCG gains absorb brought LTCG losses
        used_b_l = 0.0
        if l > 0 and carry_l:
            for lot in carry_l:
                take = min(lot[1], l)
                lot[1] -= take
                l -= take
                used_b_l += take
            carry_l = [lot for lot in carry_l if lot[1] > 1e-9]
        # 5. new losses from this year join the carry (incl. the STCG loss already used vs LTCG
        #    is consumed; only the unconsumed part carries)
        if s < 0:
            carry_s.append([0.0, -s])
        if l < 0:
            carry_l.append([0.0, -l])
        # 6. taxable + exemption
        exemption_used = min(max(l, 0.0), LTCG_EXEMPTION)
        taxable_l = max(0.0, l - LTCG_EXEMPTION)
        tax = max(0.0, s) * STCG_RATE + taxable_l * LTCG_RATE
        # 7. age one year for this FY itself; expiry check (accumulates with any gap-year
        #    expiry found in step 0). Boundary approximated by processing year — see header.
        for lot in carry_s + carry_l:
            lot[0] += 1.0
        expired_s += sum(lot[1] for lot in carry_s if lot[0] >= SET_OFF_YEARS)
        expired_l += sum(lot[1] for lot in carry_l if lot[0] >= SET_OFF_YEARS)
        carry_s = [lot for lot in carry_s if lot[0] < SET_OFF_YEARS]
        carry_l = [lot for lot in carry_l if lot[0] < SET_OFF_YEARS]
        note = f"expired after {SET_OFF_YEARS}y: STCG {expired_s:,.0f} LTCG {expired_l:,.0f}" \
            if expired_s + expired_l > 1 else ""
        out.append(FySetOff(
            fy, round(per_fy[fy]["stcg"], 2), round(per_fy[fy]["ltcg"], 2),
            round(used_b_s, 2), round(used_b_l, 2),
            round(max(0.0, s), 2), round(taxable_l, 2), round(exemption_used, 2),
            round(tax, 2),
            round(sum(lot[1] for lot in carry_s), 2), round(sum(lot[1] for lot in carry_l), 2),
            note,
        ))
    return out
