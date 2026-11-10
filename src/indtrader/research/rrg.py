"""Clean-room Relative Rotation Graph math (idea from JdK RRG; the audited repo had no
license and was broken, so only the CONCEPT is re-implemented here).

RS-Ratio   = 100 * EMA(RS) / rolling-mean(EMA(RS), m)   -> centered on 100
RS-Momentum= 100 + 100 * EMA(ROC(RS-Ratio, k))          -> centered on 100
Quadrants: Leading (RSR>=100, RSM>=100), Weakening (>=,<), Lagging (<,<), Improving (<,>=).

Weekly bars are the natural period for positional rotation work.
"""
from __future__ import annotations

import pandas as pd


def rs_ratio(stock: pd.Series, bench: pd.Series, m: int = 14) -> pd.Series:
    rs = stock / bench
    ema_rs = rs.ewm(span=m, adjust=False, min_periods=m).mean()
    base = ema_rs.rolling(m, min_periods=m).mean()
    return 100 * ema_rs / base


def rs_momentum(rsr: pd.Series, m: int = 14, k: int = 10) -> pd.Series:
    roc = (rsr - rsr.shift(k)) / rsr.shift(k)
    ema_roc = roc.ewm(span=m, adjust=False, min_periods=m).mean()
    return 100 + 100 * ema_roc


def quadrant(rsr: float, rsm: float) -> str:
    if pd.isna(rsr) or pd.isna(rsm):
        return "NA"
    if rsr >= 100:
        return "Leading" if rsm >= 100 else "Weakening"
    return "Improving" if rsm >= 100 else "Lagging"


def rrg_table(stocks: dict[str, pd.Series], bench: pd.Series, m: int = 14, k: int = 10) -> pd.DataFrame:
    """Latest RRG snapshot for a dict of close series vs a benchmark."""
    rows = []
    for sym, closes in stocks.items():
        joined = pd.concat([closes, bench], axis=1, join="inner").dropna()
        if len(joined) < max(m, k) + 5:
            continue
        rsr = rs_ratio(joined.iloc[:, 0], joined.iloc[:, 1], m)
        rsm = rs_momentum(rsr, m, k)
        rows.append(
            {
                "symbol": sym,
                "rs_ratio": round(float(rsr.iloc[-1]), 2),
                "rs_momentum": round(float(rsm.iloc[-1]), 2),
                "quadrant": quadrant(float(rsr.iloc[-1]), float(rsm.iloc[-1])),
                "prev_quadrant": quadrant(float(rsr.iloc[-2]), float(rsm.iloc[-2])),
            }
        )
    return pd.DataFrame(rows).sort_values("rs_ratio", ascending=False).reset_index(drop=True)


def transitions(history: pd.DataFrame, symbol: str) -> list[tuple[str, str]]:
    """From a long-form history of (dt, symbol, quadrant): the transition events
    (e.g. Improving -> Leading) — the actionable RRG signal."""
    s = history[history["symbol"] == symbol].sort_values("dt")["quadrant"].tolist()
    return [(a, b) for a, b in zip(s, s[1:]) if a != b]
