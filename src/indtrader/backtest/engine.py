"""Daily-bar event backtester with Indian costs and next-bar-open fills.

Two rules that the audited repos taught us:
- Fill at the NEXT bar's open, never the same bar's close that produced the signal.
- Every fill pays DeliveryCosts; every exit honours slippage. Numbers here are meant
  to be pessimistic-upper-truth, not marketing.

Risk exits (per position): ATR trailing/volatile stop, optional time stop.
Index-level literature says stops add little (Smith & N. 2013) — single stocks gap,
so we keep them for capital preservation; compare with/without in research runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from indtrader.backtest.costs import DeliveryCosts, buy_qty_for
from indtrader.backtest.strategies import Strategy
from indtrader.ta import atr

TRADING_DAYS = 252


@dataclass
class Trade:
    entry_date: pd.Timestamp
    entry_price: float
    qty: int
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    buy_cost: float = 0.0
    sell_cost: float = 0.0

    @property
    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        gross = (self.exit_price - self.entry_price) * self.qty
        return gross - self.buy_cost - self.sell_cost

    @property
    def ret(self) -> float:
        basis = self.entry_price * self.qty + self.buy_cost
        return self.pnl / basis if basis else 0.0

    @property
    def days(self) -> int:
        if self.exit_date is None:
            return 0
        return int((self.exit_date - self.entry_date).days)


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    trades: list[Trade]
    equity: pd.Series

    def summary(self) -> dict:
        eq = self.equity
        if eq.empty or len(eq) < 2:
            return {"trades": len(self.trades)}
        years = (eq.index[-1] - eq.index[0]).days / 365.25
        total = eq.iloc[-1] / eq.iloc[0] - 1
        cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else float("nan")
        daily = eq.pct_change().dropna()
        sharpe = float(daily.mean() / daily.std(ddof=0) * np.sqrt(TRADING_DAYS)) if daily.std(ddof=0) > 0 else float("nan")
        peak = eq.cummax()
        max_dd = float((eq / peak - 1).min())
        closed = [t for t in self.trades if t.exit_price is not None]
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        return {
            "start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
            "years": round(years, 2), "trades": len(closed),
            "total_return": round(float(total), 4), "cagr": round(float(cagr), 4),
            "sharpe": round(sharpe, 2), "max_drawdown": round(max_dd, 4),
            "win_rate": round(len(wins) / len(closed), 3) if closed else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "avg_win": round(gross_win / len(wins), 2) if wins else None,
            "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
            "avg_days": round(sum(t.days for t in closed) / len(closed), 1) if closed else None,
            "total_costs": round(sum(t.buy_cost + t.sell_cost for t in closed), 2),
            "buy_hold_cagr": None,  # filled by callers that hold the close series
        }


@dataclass
class _OpenPos:
    trade: Trade
    stop: float
    highest_close: float


class Backtester:
    """Single-symbol, long-only, fractional-capital backtester.

    Sizing: `sizer(atr_val, close, capital, cash) -> qty`. Default uses up to
    max_alloc of equity with the ATR stop defining the risk; capped by cash.
    """

    def __init__(
        self,
        costs: DeliveryCosts | None = None,
        capital: float = 1_000_000.0,
        atr_period: int = 14,
        stop_atr_mult: float = 3.0,
        time_stop_days: int | None = None,
        max_alloc: float = 0.10,
        sizer: Callable[[float, float, float, float], int] | None = None,
    ):
        self.costs = costs or DeliveryCosts()
        self.capital = capital
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.time_stop_days = time_stop_days
        self.max_alloc = max_alloc
        self.sizer = sizer

    def run(self, df: pd.DataFrame, strategy: Strategy, symbol: str = "") -> BacktestResult:
        if df.empty or "close" not in df:
            raise ValueError("empty frame")
        sig = strategy.signals(df).fillna(0).astype("int8")
        _atr = atr(df, self.atr_period)
        o = df["open"].to_numpy(float)
        h = df["high"].to_numpy(float)
        l = df["low"].to_numpy(float)
        c = df["close"].to_numpy(float)
        a = _atr.to_numpy(float)
        s = sig.to_numpy(int)
        idx = df.index

        cash = self.capital
        pos: _OpenPos | None = None
        trades: list[Trade] = []
        equity_vals = np.empty(len(df))

        def close_pos(i: int, price: float, reason: str) -> None:
            nonlocal cash, pos
            assert pos is not None
            fill_price = price * (1 - self.costs.slippage)
            turnover = fill_price * pos.trade.qty
            cost = self.costs.sell_costs(turnover)
            cash += turnover - cost
            pos.trade.exit_date = idx[i]
            pos.trade.exit_price = fill_price
            pos.trade.exit_reason = reason
            pos.trade.sell_cost = cost
            trades.append(pos.trade)
            pos = None

        for i in range(len(df)):
            # 1) manage open position using TODAY's bar (position was entered earlier)
            if pos is not None:
                prev_close = c[i - 1] if i else c[i]
                if not np.isnan(a[i]):
                    pos.stop = max(pos.stop, c[i] - self.stop_atr_mult * a[i])
                held_days = (idx[i] - pos.trade.entry_date).days
                if l[i] <= pos.stop and i > 0 and pos.trade.entry_date < idx[i]:
                    close_pos(i, min(pos.stop, o[i]) if l[i] < pos.stop else pos.stop, "stop")
                elif self.time_stop_days and held_days >= self.time_stop_days:
                    close_pos(i, c[i], "time_stop")
                elif s[i] == 0 and pos.trade.entry_date < idx[i]:
                    close_pos(i, c[i], "signal_exit")

            # 2) act on yesterday's signal at TODAY's open (next-bar-open fills)
            want_long = i > 0 and s[i - 1] == 1 and not np.isnan(a[i - 1])
            if want_long and pos is None:
                entry_price = o[i] * (1 + self.costs.slippage)
                equity_now = cash + (pos.trade.entry_price * pos.trade.qty if pos else 0.0)
                alloc = self.max_alloc * equity_now
                if self.sizer is not None:
                    qty = self.sizer(a[i - 1], entry_price, equity_now, cash)
                else:
                    risk_amount = 0.02 * equity_now
                    per_share_risk = self.stop_atr_mult * a[i - 1]
                    qty = int(min(risk_amount / per_share_risk, alloc / entry_price))
                qty = min(qty, buy_qty_for(cash, entry_price, self.costs))
                if qty > 0:
                    turnover = qty * entry_price
                    cost = self.costs.buy_costs(turnover)
                    cash -= turnover + cost
                    pos = _OpenPos(
                        trade=Trade(entry_date=idx[i], entry_price=entry_price, qty=qty, buy_cost=cost),
                        stop=entry_price - self.stop_atr_mult * a[i - 1],
                        highest_close=c[i],
                    )

            mark = c[i] * pos.trade.qty if pos else 0.0
            equity_vals[i] = cash + mark

        if pos is not None:  # liquidate at last close for clean equity math
            close_pos(len(df) - 1, c[-1], "end_of_test")

        eq = pd.Series(equity_vals, index=idx, name="equity")
        return BacktestResult(symbol=symbol, strategy=strategy.name, trades=trades, equity=eq)
