"""Portfolio weights — deterministic core first, PyPortfolioOpt on top when wanted.

inverse_vol is the honest default: no optimizer, no matrix inversion, no luck —
w_i ∝ 1/σ_i capped per symbol, residual to cash. HRP (PyPortfolioOpt) is offered
because the plan asked for it; it is clustered-shrunk weights, not a return
forecaster, and if the dependency is missing we SAY SO and fall back, never fake it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 250


def clean_returns(prices: dict[str, pd.Series], min_obs: int = 60) -> pd.DataFrame:
    joint = pd.concat({s: p.dropna() for s, p in prices.items()}, axis=1, join="inner").dropna()
    if len(joint) < min_obs:
        raise ValueError(f"need >= {min_obs} joint sessions, have {len(joint)} — refusing to size on thin air")
    return joint.pct_change().dropna()


def inverse_vol(rets: pd.DataFrame, max_w: float = 0.25) -> dict[str, float]:
    sig = rets.std(ddof=1).replace(0.0, np.nan)
    if sig.isna().any():  # a flat series would get infinite weight — refuse instead
        raise ValueError(f"zero-variance series: {list(sig[sig.isna()].index)}")
    w = (1.0 / sig) / (1.0 / sig).sum()
    return cap_weights(w.to_dict(), max_w)


def cap_weights(w: dict[str, float], max_w: float) -> dict[str, float]:
    """Cap at max_w, redistribute overflow proportionally to uncapped, iterate."""
    out = dict(w)
    for _ in range(100):
        over = {k: v for k, v in out.items() if v > max_w + 1e-12}
        if not over:
            break
        excess = sum(v - max_w for v in over.values())
        for k in over:
            out[k] = max_w
        under = {k: v for k, v in out.items() if v < max_w - 1e-12}
        s = sum(under.values())
        if s <= 0:  # everything capped => sums to <1, remainder is cash — acceptable
            break
        for k in under:
            out[k] += excess * under[k] / s
    return {k: round(v, 4) for k, v in out.items()}


def hrp(rets: pd.DataFrame, max_w: float = 0.25) -> dict[str, float]:
    """Hierarchical Risk Parity via PyPortfolioOpt; raises ImportError for ANY
    unusable state (missing lib OR lib-vs-scipy breakage like the 1.6.0/scipy>=1.15
    `_LINKAGE_METHODS` removal found in testing) — callers decide the fallback."""
    try:
        from pypfopt import HRPOpt

        opt = HRPOpt(rets)
        w = opt.optimize() or {}
        if not w:
            raise RuntimeError("HRPOpt returned no weights")
    except ImportError as e:
        raise ImportError(f"HRP needs PyPortfolioOpt: pip install PyPortfolioOpt ({e})") from e
    except Exception as e:  # installed but broken with this scipy build — say so, don't crash the book
        raise ImportError(f"HRP unusable with installed versions ({type(e).__name__}: {e})") from e
    return cap_weights({k: float(v) for k, v in w.items()}, max_w)


def weights(prices: dict[str, pd.Series], method: str = "inverse_vol",
            max_w: float = 0.25, min_obs: int = 60) -> tuple[dict[str, float], str]:
    """(weights, method_actually_used) — the second value tells the truth when HRP
    was requested but the library is missing."""
    rets = clean_returns(prices, min_obs=min_obs)
    if method == "hrp":
        try:
            return hrp(rets, max_w), "hrp"
        except ImportError as e:
            print(f"[weights] HRP unavailable ({e}) — falling back to inverse_vol "
                  "(this is a downgrade, not the same thing)")
            return inverse_vol(rets, max_w), "inverse_vol (fallback)"
    if method != "inverse_vol":
        raise ValueError(f"unknown method '{method}' (inverse_vol|hrp)")
    return inverse_vol(rets, max_w), "inverse_vol"
