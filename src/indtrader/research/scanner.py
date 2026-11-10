"""Universe scanner: one evidence row per symbol, then a ranked shortlist.

This is the "what should I even look at tonight" step of the daily workflow.
Every column is deterministic and computable from closed daily bars — the AI
layer (if any) annotates this table later, it never feeds it.

Scoring is deliberately simple and literature-aligned, in this order:
  1. regime gate        — nothing is interesting when the light is OFF
  2. trend gate         — close > SMA200 (Faber); below = not a swing long, full stop
  3. relative strength  — RRG quadrant (Improving/Leading preferred, clean-room rrg.py)
  4. absolute momentum  — 6-month return rank (Antonacci relative leg)
Volatility (20d annualized) is shown, not scored — position sizing handles it
(sizing.size_position uses ATR, Barroso & Santa-Clara spirit).
"""
from __future__ import annotations

import pandas as pd

from indtrader.research.regime import Regime
from indtrader.research.rrg import quadrant, rs_momentum, rs_ratio
from indtrader.ta import annualized_vol, rsi, sma

QUADRANT_SCORE = {"Improving": 2.0, "Leading": 1.5, "Weakening": 0.0, "Lagging": -1.0, "NA": 0.0}


def evidence_row(symbol: str, closes: pd.Series, bench: pd.Series | None) -> dict:
    """One symbol's full evidence record. NaN-tolerant: short history just scores 0."""
    c = closes.dropna()
    row: dict = {"symbol": symbol}
    if len(c) < 210:  # need a 200-DMA gate to say anything at all
        row.update({"close": float(c.iloc[-1]) if len(c) else None, "tradable": False,
                    "note": f"only {len(c)} bars"})
        return row
    s200 = sma(c, 200)
    s50 = sma(c, 50)
    above = bool(c.iloc[-1] > s200.iloc[-1])
    q = "NA"
    if bench is not None:
        joined = pd.concat([c, bench], axis=1, join="inner").dropna()
        if len(joined) >= 30:
            rsr = rs_ratio(joined.iloc[:, 0], joined.iloc[:, 1])
            rsm = rs_momentum(rsr)
            q = quadrant(float(rsr.iloc[-1]), float(rsm.iloc[-1]))
    mom6 = float(c.pct_change(126).iloc[-1])
    row.update(
        {
            "close": round(float(c.iloc[-1]), 2),
            "sma50": round(float(s50.iloc[-1]), 2),
            "sma200": round(float(s200.iloc[-1]), 2),
            "above_200dma": above,
            "rsi14": round(float(rsi(c, 14).iloc[-1]), 1),
            "mom_6m": round(mom6, 4),
            "annvol": round(float(annualized_vol(c, 20).iloc[-1]), 3),
            "quadrant": q,
            "tradable": above,
            "note": "",
        }
    )
    return row


def scan(closes: dict[str, pd.Series], bench: pd.Series | None = None,
         regime: Regime | None = None, top: int = 10) -> pd.DataFrame:
    """Evidence table for the whole universe, best candidates first."""
    rows = [evidence_row(sym, c, bench) for sym, c in sorted(closes.items())]
    df = pd.DataFrame(rows)
    df["score"] = 0.0
    ok = df["tradable"] == True  # noqa: E712
    df.loc[ok, "score"] += df.loc[ok, "quadrant"].map(QUADRANT_SCORE).fillna(0.0)
    if ok.any():
        df.loc[ok, "score"] += df.loc[ok, "mom_6m"].rank(pct=True)
    if regime is not None:
        df["exposure_scale"] = regime.exposure_scale
    df = df.sort_values(["tradable", "score"], ascending=False).reset_index(drop=True)
    return df.head(top) if top else df
