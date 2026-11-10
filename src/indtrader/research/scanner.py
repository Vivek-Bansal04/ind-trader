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
from indtrader.ta import annualized_vol, atr, ema, rsi, sma

QUADRANT_SCORE = {"Improving": 2.0, "Leading": 1.5, "Weakening": 0.0, "Lagging": -1.0, "NA": 0.0}

# Compression ("base" quality): 20-session close range divided by ATR(14) — scale-free,
# works the same on a ₹50 and a ₹5,000 name (concept from the nse-stock-analysis audit).
# Pure evidence: a tight base ANNOTATES, never boosts — it can resolve either way, and
# guessing a directional weight for it is the fudge we reject.
COMP_RANGE_ATR = 0.90
COMP_EMA_DRIFT = 0.03
COMP_LOOKBACK = 20


def compression_ratio(closes: pd.Series, bars: pd.DataFrame | None) -> float | None:
    c = closes.dropna()
    if bars is None or len(c) < COMP_LOOKBACK:
        return None
    b = bars.tail(COMP_LOOKBACK)
    a = float(atr(b, 14).iloc[-1])
    if not a or pd.isna(a):
        return None
    cw = c.tail(COMP_LOOKBACK)
    return (float(cw.max()) - float(cw.min())) / a


def is_compressing(closes: pd.Series, bars: pd.DataFrame | None) -> bool:
    """Tight range AND price parked near the 21-EMA: a base, not a flagging downtrend.
    (Weekly-band idea from the Enigma_24 audit, clean-room, applied daily without ever
    being allowed to gate anything.)"""
    ratio = compression_ratio(closes, bars)
    if ratio is None or ratio > COMP_RANGE_ATR:
        return False
    c = closes.dropna()
    e = float(ema(c, 21).iloc[-1])
    return bool(e and abs(float(c.iloc[-1]) / e - 1.0) <= COMP_EMA_DRIFT)


# ₹/day average turnover over the 20 sessions BEFORE the signal day (close*volume proxy).
# A swing position must be enterable and exitable; one spike-day of volume cannot launder
# an illiquid name into the shortlist — the window deliberately ends at the signal, not after.
LIQUIDITY_FLOOR_TURNOVER = 2e7  # 2 crore/day


def liquidity_ok(closes: pd.Series, volume: pd.Series | None) -> tuple[bool, float | None]:
    """(passes_floor, avg_turnover). Missing volume data -> (True, None): unknown does
    not disqualify here — it only qualifies loudly when present and below the floor."""
    if volume is None:
        return True, None
    joined = pd.concat([closes.dropna(), volume.dropna()], axis=1, join="inner").tail(20)
    if len(joined) < 10:
        return True, None
    turn = float((joined.iloc[:, 0] * joined.iloc[:, 1]).mean())
    return turn >= LIQUIDITY_FLOOR_TURNOVER, turn


def evidence_row(symbol: str, closes: pd.Series, bench: pd.Series | None,
                 extra: dict | None = None, volume: pd.Series | None = None,
                 bars: pd.DataFrame | None = None) -> dict:
    """One symbol's full evidence record. NaN-tolerant: short history just scores 0.

    `extra` carries store-derived feeds (delivery %, net bulk value) — attached and
    scored, but only when actually ingested (None = unknown, which scores nothing).
    `bars` (full OHLCV frame) enables the compression column; without it, unknown.
    """
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
    if extra:
        row["deliv_per"] = extra.get("deliv_per")
        row["deliv_avg20"] = extra.get("deliv_avg")
        row["net_bulk"] = extra.get("net_bulk")
        # a breakout on thin delivery is mostly churning — flag for the human eye
        dp, da = extra.get("deliv_per"), extra.get("deliv_avg")
        if dp is not None and da is not None and above and dp < min(da, 40.0):
            row["note"] = "low delivery on trend"
    liq_ok, turn = liquidity_ok(c, volume)
    row["turnover_20d"] = None if turn is None else round(turn)
    if not liq_ok:
        row["tradable"] = False
        row["note"] = (row["note"] + "; " if row["note"] else "") + "illiquid (<2Cr/day)"
    ratio = compression_ratio(c, bars)
    row["base_quality"] = ("tight" if (ratio is not None and is_compressing(c, bars))
                           else "wide" if ratio is not None else None)
    if row["base_quality"] == "tight" and above:
        row["note"] = (row["note"] + "; " if row["note"] else "") + "compressed base"
    return row


def scan(closes: dict[str, pd.Series], bench: pd.Series | None = None,
         regime: Regime | None = None, top: int = 10,
         extras: dict[str, dict] | None = None,
         volumes: dict[str, pd.Series] | None = None,
         bars: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Evidence table for the whole universe, best candidates first.

    extras: {symbol: {deliv_per, deliv_avg, net_bulk}} — from data.feeds readers.
    Only net positive bulk buying boosts (+0.5); low delivery only annotates.
    Missing feeds score/annotate nothing — unknown must never look like evidence.
    volumes: {symbol: volume series} — feeds the liquidity floor; absent = unknown,
    which never disqualifies but is shown as such (turnover_20d = None).
    bars: {symbol: OHLCV frame} — feeds the compression column; same unknown rule.
    """
    rows = [evidence_row(sym, c, bench, extra=(extras or {}).get(sym),
                         volume=(volumes or {}).get(sym), bars=(bars or {}).get(sym))
            for sym, c in sorted(closes.items())]
    df = pd.DataFrame(rows)
    df["score"] = 0.0
    ok = df["tradable"] == True  # noqa: E712
    df.loc[ok, "score"] += df.loc[ok, "quadrant"].map(QUADRANT_SCORE).fillna(0.0)
    if ok.any():
        df.loc[ok, "score"] += df.loc[ok, "mom_6m"].rank(pct=True)
    if extras:
        boost = df["symbol"].map(lambda s: 0.5 if ((extras.get(s) or {}).get("net_bulk") or 0) > 0 else 0.0)
        df.loc[ok, "score"] += boost[ok]
    if regime is not None:
        df["exposure_scale"] = regime.exposure_scale
    df = df.sort_values(["tradable", "score"], ascending=False).reset_index(drop=True)
    return df.head(top) if top else df
