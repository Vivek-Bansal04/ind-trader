"""Indian delivery (CNC) cost model — the gap that made audited backtests meaningless.

Sources: current NSE/BSE + SEBI schedules for the equity cash segment (Nov 2026).
Everything is conservative-high; correct the constants in one place when slabs change.
Brokerage default is Zerodha/Angel-style delivery: 0% (or flat 200/order) — the
brokerage_rate knob covers percentage brokers too.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil

# --- embedded constants must carry an as-of date; tools/check_freshness.py tracks them
RATES_AS_OF = "2026-11-01"  # NSE/SEBI cash-segment schedule date used below

STT_BUY = 0.001          # 0.1% on buy (delivery, intraday same)
STT_SELL = 0.001         # 0.1% on sell
EXCHANGE_TXN = 0.0000297  # NSE 0.00297% (turnover charges); BSE similar
SEBI = 0.000001           # 10 per crore
STAMP_DUTY_BUY = 0.00015  # 0.015% on buy
CGST_ON_CHARGES = 0.18    # 18% GST on (brokerage + exchange txn + SEBI)
SLBM_BUY = 0.00003        # approx, left in conservatively for delivery buys
DEFAULT_SLIPPAGE = 0.0005  # 5 bps per side


@dataclass(frozen=True)
class DeliveryCosts:
    brokerage_rate: float = 0.0      # fraction of turnover per side (0 for discount brokers' CNC)
    brokerage_cap: float = 200.0     # per-order cap if brokerage_rate > 0
    stt_buy: float = STT_BUY
    stt_sell: float = STT_SELL
    exchange_txn: float = EXCHANGE_TXN
    sebi: float = SEBI
    stamp_duty_buy: float = STAMP_DUTY_BUY
    cgst_on_charges: float = CGST_ON_CHARGES
    slbm_buy: float = SLBM_BUY
    slippage: float = DEFAULT_SLIPPAGE  # applied by moving the fill price, not the cost

    def buy_costs(self, turnover: float, brokerage_override: float | None = None) -> float:
        brokerage = self._brokerage(turnover, brokerage_override)
        txn = turnover * (self.exchange_txn + self.sebi)
        gst = (brokerage + txn) * self.cgst_on_charges
        return brokerage + txn + gst + turnover * (self.stt_buy + self.stamp_duty_buy + self.slbm_buy)

    def sell_costs(self, turnover: float, brokerage_override: float | None = None) -> float:
        brokerage = self._brokerage(turnover, brokerage_override)
        txn = turnover * (self.exchange_txn + self.sebi)
        gst = (brokerage + txn) * self.cgst_on_charges
        return brokerage + txn + gst + turnover * self.stt_sell

    def round_trip_fraction(self, notional: float) -> float:
        """All-in buy+sell costs as a fraction of one side's notional."""
        return (self.buy_costs(notional) + self.sell_costs(notional)) / notional

    def _brokerage(self, turnover: float, override: float | None = None) -> float:
        if override is not None:
            return override
        if self.brokerage_rate <= 0:
            return 0.0
        return min(turnover * self.brokerage_rate, self.brokerage_cap)


def buy_qty_for(cash: float, price: float, costs: DeliveryCosts) -> int:
    """Lot-style rounding down to whole shares, guaranteeing cash covers costs (lot of 1 share;
    exchange lot sizes are an F&O concept — delivery is 1-share lots)."""
    if price <= 0:
        return 0
    qty = int(cash / price)
    while qty > 0 and qty * price + costs.buy_costs(qty * price) > cash:
        qty -= 1
    return qty


def lot_ceil_1(qty: float) -> int:
    return int(ceil(qty))
