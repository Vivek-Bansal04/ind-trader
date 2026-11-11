"""Strategy-decay monitor (plan §D: live/paper vs the Monte-Carlo expectation band).

Question it answers, daily during Phase 4: is the paper book performing like the
backtest's own bootstrap says it plausibly could, or has it left the distribution?
A paper strategy underperforming the p5 band of ITS OWN costed backtest is evidence
of decay/regime-shift, not bad luck — the plan's rule then is: flag -> human review.
This module never auto-trades anything (nothing here could).

Method: resample the backtest's realized trade PnLs with replacement k times
(k = number of paper trades so far) -> capital band [p5, p95] for after k trades.
Compare the actual paper cumulative PnL. Also scan rolling windows of paper trades
so slow decay shows up before the all-time sum moves.

Honesty rules shared with montecarlo.py: fewer than MIN_TRADES baseline trades ->
refuse (None), never a confident band on noise; paper trades shorter than a window
are compared only at full length; every number is money, not a price forecast.
"""
from __future__ import annotations

import numpy as np

from indtrader.backtest.montecarlo import MIN_TRADES

DEFAULT_ALPHA = 0.05


def _band(baseline: np.ndarray, k: int, capital: float, n_sims: int, seed: int) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, baseline.size, size=(n_sims, k))
    final = capital + np.cumsum(baseline[idx], axis=1)[:, -1]
    return float(np.percentile(final, 100 * DEFAULT_ALPHA)), float(np.percentile(final, 100 * (1 - DEFAULT_ALPHA)))


def decay_check(baseline_trades: list[float], paper_trades: list[float],
                capital: float = 100_000.0, n_sims: int = 2000, seed: int = 12345,
                window: int | None = None) -> dict | None:
    """None = cannot judge (too little baseline) — reported as such, never a pass/fail."""
    base = np.asarray([p for p in baseline_trades if np.isfinite(p)], dtype=float)
    paper = [float(p) for p in paper_trades if np.isfinite(p)]
    if base.size < MIN_TRADES:
        return None
    n = len(paper)
    if n == 0:
        return {"status": "no-paper-trades-yet", "baseline_trades": int(base.size)}

    windows: list[dict] = []
    w = min(window or 20, n)
    starts = list(range(0, max(1, n - w + 1))) + [n - w] if n > w else [0]
    for i in sorted(set(starts))[:8]:  # bounded print budget
        seg = sum(paper[i:i + w])
        lo, hi = _band(base, w, capital, n_sims, seed + i)
        actual = capital + seg
        windows.append({"trades": w, "window_end_idx": i + w, "actual": round(actual, 2),
                        "band_p5": round(lo, 2), "band_p95": round(hi, 2),
                        "below_p5": actual < lo, "above_p95": actual > hi})

    lo, hi = _band(base, n, capital, n_sims, seed)
    actual_all = capital + sum(paper)
    below = actual_all < lo
    return {"status": "REVIEW — below own expectation band" if below else "within band",
            "baseline_trades": int(base.size), "paper_trades": n,
            "actual_after_all": round(actual_all, 2),
            "band_after_all": [round(lo, 2), round(hi, 2)],
            "below_p5_all": below, "windows": windows}
