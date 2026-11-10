"""Hard limits + kill-switch: the deterministic gate that runs BEFORE any strategy or AI.
Bypass is not a supported code path (lesson copied from india-trade-cli's risk_limits)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


class RiskBlocked(RuntimeError):
    pass


@dataclass
class RiskManager:
    """Daily-loss and per-symbol caps, checked against a simple ledger of realized P&L.

    mode is 'paper'|'live'. In live mode an armed kill-switch blocks everything.
    """

    max_daily_loss: float = 20_000.0
    max_positions: int = 15
    max_per_symbol: int = 1
    mode: str = "paper"
    kill_switch: bool = False

    def check_new_position(
        self,
        symbol: str,
        open_positions: dict[str, int],
        realized_pnl_today: float,
        now: datetime | None = None,
    ) -> None:
        """Raise RiskBlocked with a human reason if this order must not happen."""
        if self.kill_switch:
            raise RiskBlocked("KILL-SWITCH armed: no new orders until manually disarmed.")
        if self.mode == "live" and realized_pnl_today <= -abs(self.max_daily_loss):
            raise RiskBlocked(
                f"daily loss limit hit ({realized_pnl_today:,.0f} <= -{self.max_daily_loss:,.0f}); no new positions today."
            )
        if symbol in open_positions and open_positions[symbol] >= self.max_per_symbol:
            raise RiskBlocked(f"already holding {symbol}; per-symbol cap {self.max_per_symbol}.")
        if len(open_positions) >= self.max_positions:
            raise RiskBlocked(f"open-positions cap {self.max_positions} reached.")

    @staticmethod
    def no_stale_data(latest_bar_date: date, today: date, max_stale_days: int = 3) -> None:
        """Dead-man check: don't trade on old data (weekends tolerated)."""
        if (today - latest_bar_date).days > max_stale_days:
            raise RiskBlocked(f"data stale: latest bar {latest_bar_date}, today {today}. Fix ETL before trading.")
