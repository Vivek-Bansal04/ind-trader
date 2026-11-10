"""Monte Carlo over realised (costed) trades — what the plan's Phase 2 asks for.

Resamples the trade PnL sequence with replacement and rebuilds equity paths, to see
the DISTRIBUTION of outcomes a strategy could have produced, not the one history
happened to show. Order-independent by design: sequence luck is a real risk and an
equity curve drawn in one order hides it.

Honesty rules: bootstrap assumes trade exchangeability (it isn't strictly true in
regimes — this measures parameterisation risk, not a crystal ball); it NEVER resamples
prices and never invents trades; fewer than MIN_TRADES closed trades -> refuses (None)
instead of printing a confident distribution built on noise.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_TRADES = 20


def bootstrap_paths(trade_pnl: list[float] | np.ndarray, capital: float,
                    n_sims: int = 1000, seed: int = 12345) -> np.ndarray | None:
    pnl = np.asarray([p for p in trade_pnl if np.isfinite(p)], dtype=float)
    if pnl.size < MIN_TRADES:
        return None  # unknown, not zero: too little to resample honestly
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, pnl.size, size=(n_sims, pnl.size))
    return capital + np.cumsum(pnl[idx], axis=1)


def summarize(paths: np.ndarray | None, capital: float) -> dict | None:
    if paths is None:
        return None
    final = paths[:, -1]
    running_max = np.maximum.accumulate(paths, axis=1)
    dd = (paths / running_max - 1.0).min(axis=1)  # worst dip per sim (negative)
    return {
        "sims": int(paths.shape[0]),
        "trades_per_sim": int(paths.shape[1]),
        "final_p5": round(float(np.percentile(final, 5)), 2),
        "final_p50": round(float(np.percentile(final, 50)), 2),
        "final_p95": round(float(np.percentile(final, 95)), 2),
        "prob_loss": round(float((final < capital).mean()), 3),
        "maxdd_median": round(float(np.percentile(dd, 50)), 4),
        "maxdd_worst5pct": round(float(np.percentile(dd, 5)), 4),  # the pain tail
    }


def monte_carlo(result, capital: float | None = None, n_sims: int = 1000,
                seed: int = 12345) -> dict:
    """Accepts BacktestResult or PortfolioResult (anything with .trades having .pnl and
    .equity). Returns the distribution summary; reason why not, if it refuses."""
    pnl = [float(t.pnl) for t in getattr(result, "trades", [])
           if getattr(t, "exit_price", None) is not None and np.isfinite(getattr(t, "pnl", np.nan))]
    eq = getattr(result, "equity", None)
    cap = capital if capital is not None else (float(eq.iloc[0]) if eq is not None and len(eq) else 1_000_000.0)
    paths = bootstrap_paths(pnl, cap, n_sims=n_sims, seed=seed)
    if paths is None:
        return {"ok": False, "reason": f"only {len(pnl)} closed trades — refuse to bootstrap "
                                       f"on < {MIN_TRADES} trades (this is an unknown, not a pass)"}
    return {"ok": True, "capital": cap, **summarize(paths, cap)}
