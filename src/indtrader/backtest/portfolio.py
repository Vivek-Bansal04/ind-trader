"""Portfolio-level daily backtest: N symbols, one cash pool, concurrent positions.

The single-symbol engine answers "does this setup work?"; this answers "what does a
book of them actually do with shared cash?" — the only version comparable to a real
account equity curve.

No-lookahead rules (same spirit as the single-symbol engine):
- decisions on bar i's CLOSE act at bar i+1's OPEN;
- the regime scale used at an entry on day t is computed from bars strictly before t;
- marks use each symbol's last close on/before the day (never a future bar).
Costs: every fill pays DeliveryCosts (conservative Indian set by default).

Slot contention is resolved by candidate score (6-month momentum, tie -> lower
vol gets in when momentum ties... higher momentum first). Deliberately simple.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from indtrader.backtest.costs import DeliveryCosts, buy_qty_for
from indtrader.backtest.strategies import Strategy
from indtrader.research.regime import classify
from indtrader.ta import annualized_vol, atr, sma

TRADING_DAYS = 252


@dataclass
class PTrade:
    symbol: str
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
        return (self.exit_price - self.entry_price) * self.qty - self.buy_cost - self.sell_cost


@dataclass
class _Pos:
    trade: PTrade
    stop: float


@dataclass
class PortfolioResult:
    equity: pd.Series
    trades: list[PTrade]
    exposure: pd.Series   # invested market value / equity
    n_open: pd.Series     # concurrent open positions per day

    def summary(self) -> dict:
        eq = self.equity
        if eq.empty or len(eq) < 2:
            return {"trades": 0}
        years = (eq.index[-1] - eq.index[0]).days / 365.25
        total = eq.iloc[-1] / eq.iloc[0] - 1
        cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else float("nan")
        daily = eq.pct_change().dropna()
        sharpe = float(daily.mean() / daily.std(ddof=0) * np.sqrt(TRADING_DAYS)) if daily.std(ddof=0) > 0 else float("nan")
        max_dd = float((eq / eq.cummax() - 1).min())
        closed = [t for t in self.trades if t.exit_price is not None]
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        gw, gl = sum(t.pnl for t in wins), abs(sum(t.pnl for t in losses))
        return {
            "start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
            "years": round(years, 2), "closed_trades": len(closed),
            "total_return": round(float(total), 4), "cagr": round(float(cagr), 4),
            "sharpe": round(sharpe, 2), "max_drawdown": round(max_dd, 4),
            "win_rate": round(len(wins) / len(closed), 3) if closed else None,
            "profit_factor": round(gw / gl, 2) if gl > 0 else None,
            "avg_gross_exposure": round(float(self.exposure.mean()), 3),
            "avg_open_positions": round(float(self.n_open.mean()), 2),
            "total_costs": round(sum(t.buy_cost + t.sell_cost for t in closed), 2),
        }


class _Sym:
    """Per-symbol precomputed arrays on its own index, with date->row lookup."""

    def __init__(self, symbol: str, df: pd.DataFrame, strategy: Strategy, atr_period: int):
        self.symbol = symbol.upper()
        self.index = df.index
        self.pos_of = {d: i for i, d in enumerate(self.index)}
        self.sig = strategy.signals(df).fillna(0).astype("int8").to_numpy(int)
        self.atr = atr(df, atr_period).to_numpy(float)
        self.open = df["open"].to_numpy(float)
        self.low = df["low"].to_numpy(float)
        self.close = df["close"].to_numpy(float)
        self.vol = annualized_vol(df["close"], 20).to_numpy(float)
        self.mom6 = (df["close"] / df["close"].shift(126) - 1).to_numpy(float)


class PortfolioBacktester:
    def __init__(
        self,
        costs: DeliveryCosts | None = None,
        capital: float = 1_000_000.0,
        max_positions: int = 5,
        max_alloc_frac: float = 0.20,
        risk_per_trade: float = 0.01,
        atr_period: int = 14,
        stop_atr_mult: float = 3.0,
        time_stop_days: int | None = None,
        use_regime: bool = True,
        bench_closes: pd.Series | None = None,
    ):
        self.costs = costs or DeliveryCosts()
        self.capital = capital
        self.max_positions = max_positions
        self.max_alloc_frac = max_alloc_frac
        self.risk_per_trade = risk_per_trade
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.time_stop_days = time_stop_days
        self.use_regime = use_regime
        self.bench = None if bench_closes is None else bench_closes.sort_index()

    def _exposure_scale(self, syms: list[_Sym], calendar: pd.DatetimeIndex) -> np.ndarray:
        """scale[t] from bars strictly before calendar[t] — searchsorted, no lookahead."""
        n = len(calendar)
        if not self.use_regime:
            return np.ones(n)
        cal_np = calendar.to_numpy("datetime64[ns]")
        above = np.full((len(syms), n), np.nan)
        for k, s in enumerate(syms):
            a = (s.close > sma(pd.Series(s.close, index=s.index), 200)).to_numpy(float)
            k_pos = np.searchsorted(s.index.values, cal_np, side="left") - 1  # last bar < t
            valid = k_pos >= 0
            above[k, valid] = np.where(np.isnan(a[k_pos[valid]]), np.nan, a[k_pos[valid]])
        valid = ~np.isnan(above)
        cnt = valid.sum(axis=0)
        breadth = np.where(cnt > 0, np.nansum(above, axis=0) / np.maximum(cnt, 1), np.nan)
        idx_up = np.full(n, np.nan)
        if self.bench is not None and len(self.bench) >= 200:
            b_up = (self.bench > sma(self.bench, 200)).to_numpy(float)
            k_pos = np.searchsorted(self.bench.index.values, cal_np, side="left") - 1
            valid = k_pos >= 0
            idx_up[valid] = b_up[k_pos[valid]]
        scale = np.empty(n)
        for t in range(n):
            br = None if np.isnan(breadth[t]) else float(breadth[t])
            up = None if np.isnan(idx_up[t]) else bool(idx_up[t] >= 1.0)
            scale[t] = classify(br, up).exposure_scale
        return scale

    def run(self, data: dict[str, pd.DataFrame], strategies: dict[str, Strategy] | Strategy) -> PortfolioResult:
        if not data:
            raise ValueError("no symbols")
        if isinstance(strategies, Strategy):
            strategies = {k: strategies for k in data}
        syms = [_Sym(s, df.sort_index(), strategies[s], self.atr_period) for s, df in sorted(data.items())]
        calendar = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for s in syms])))
        scale = self._exposure_scale(syms, calendar)
        by_name = {s.symbol: s for s in syms}
        # per-symbol last-close-as-of arrays for marking (k_pos[t] = last row index < t)
        cal_np = calendar.to_numpy("datetime64[ns]")

        def asof_k(s: _Sym) -> np.ndarray:
            k = np.searchsorted(s.index.values, cal_np, side="right") - 1
            return k

        asof = {s.symbol: asof_k(s) for s in syms}

        cash = self.capital
        positions: dict[str, _Pos] = {}
        trades: list[PTrade] = []
        eq_vals = np.empty(len(calendar))
        exp_vals = np.empty(len(calendar))
        n_open = np.empty(len(calendar))

        # asof_prev: last bar STRICTLY before the day (for decisions, no same-day peek)
        # asof_incl: last bar on/before the day (for end-of-day marking)
        asof_prev = {s.symbol: np.searchsorted(s.index.values, cal_np, side="left") - 1 for s in syms}

        for t, d in enumerate(calendar):
            # 1) manage open positions on today's bars
            for sym in list(positions):
                s = by_name[sym]
                i = s.pos_of.get(d)
                if i is None or i == 0:
                    continue
                p = positions[sym]
                if not np.isnan(s.atr[i]):
                    p.stop = max(p.stop, s.close[i] - self.stop_atr_mult * s.atr[i])
                held = (d - p.trade.entry_date).days
                exit_px, reason = None, ""
                if s.low[i] <= p.stop and p.trade.entry_date < d:
                    exit_px = min(p.stop, s.open[i]) if s.low[i] < p.stop else p.stop
                    reason = "stop"
                elif self.time_stop_days and held >= self.time_stop_days:
                    exit_px, reason = s.close[i], "time_stop"
                elif s.sig[i - 1] == 0 and p.trade.entry_date < d:
                    exit_px, reason = s.close[i], "signal_off"
                if exit_px is not None:
                    fill = exit_px * (1 - self.costs.slippage)
                    turnover = fill * p.trade.qty
                    cost = self.costs.sell_costs(turnover)
                    cash += turnover - cost
                    p.trade.exit_date, p.trade.exit_price = d, fill
                    p.trade.exit_reason, p.trade.sell_cost = reason, cost
                    trades.append(p.trade)
                    del positions[sym]

            # equity at yesterday's marks — the honest basis for today's open decisions
            mv_prev = sum(p.trade.qty * s_close_at(by_name[sym], asof_prev[sym][t])
                          for sym, p in positions.items())
            equity_prev = cash + mv_prev

            # 2) entries: yesterday's signal, today's open, momentum-first
            if len(positions) < self.max_positions:
                cands = []
                for s in syms:
                    if s.symbol in positions:
                        continue
                    i = s.pos_of.get(d)
                    if i is None or i == 0 or np.isnan(s.atr[i - 1]) or s.sig[i - 1] != 1:
                        continue
                    cands.append((0.0 if np.isnan(s.mom6[i - 1]) else s.mom6[i - 1],
                                  0.0 if np.isnan(s.vol[i - 1]) else -s.vol[i - 1], s, i))
                budget = equity_prev * scale[t]
                for _mom, _negvol, s, i in sorted(cands, key=lambda c: (c[0], c[1]), reverse=True):
                    if len(positions) >= self.max_positions:
                        break
                    entry_px = s.open[i] * (1 + self.costs.slippage)
                    per_share_risk = self.stop_atr_mult * s.atr[i - 1]
                    if per_share_risk <= 0 or per_share_risk >= entry_px:
                        continue
                    qty = int(min(self.risk_per_trade * equity_prev / per_share_risk,
                                  self.max_alloc_frac * budget / entry_px))
                    qty = min(qty, buy_qty_for(cash, entry_px, self.costs))
                    if qty <= 0:
                        continue
                    turnover = qty * entry_px
                    cost = self.costs.buy_costs(turnover)
                    cash -= turnover + cost
                    positions[s.symbol] = _Pos(
                        trade=PTrade(symbol=s.symbol, entry_date=d, entry_price=entry_px,
                                     qty=qty, buy_cost=cost),
                        stop=entry_px - per_share_risk,
                    )

            # 3) end-of-day marks (today's close where it exists)
            mv = sum(p.trade.qty * s_close_at(by_name[sym], asof[sym][t])
                     for sym, p in positions.items())
            total = cash + mv
            eq_vals[t] = total
            exp_vals[t] = mv / total if total > 0 else 0.0
            n_open[t] = len(positions)

        # close remaining positions at last known close (audit trail, not equity change)
        for sym, p in positions.items():
            s = by_name[sym]
            fill = s.close[-1] * (1 - self.costs.slippage)
            p.trade.exit_date, p.trade.exit_price = calendar[-1], fill
            p.trade.exit_reason = "end_of_test"
            p.trade.sell_cost = self.costs.sell_costs(fill * p.trade.qty)
            trades.append(p.trade)

        return PortfolioResult(
            equity=pd.Series(eq_vals, index=calendar, name="equity"),
            trades=trades,
            exposure=pd.Series(exp_vals, index=calendar, name="exposure"),
            n_open=pd.Series(n_open, index=calendar, name="n_open"),
        )


def s_close_at(s: _Sym, k: int) -> float:
    """Last close on/before a calendar day; 0.0 before the symbol's first bar."""
    return s.close[k] if k >= 0 else 0.0
