"""Walk-forward validation with an overfitting ratio, plus a parameter-plateau check.
The point: a strategy is only interesting if it survives unseen windows AND nearby
parameters. Past results remain an expectation, not a promise. (Concepts from Brown
& Prettie / Andrews walk-forward; CPCV per Lopez de Prado is a Phase-3 upgrade.)"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from indtrader.backtest.engine import Backtester
from indtrader.backtest.strategies import Strategy


@dataclass
class WFResult:
    insample_score: float
    oos_score: float
    overfit_ratio: float  # oos/ins; < ~0.5 is suspicious, < 0 is dead
    per_window: list[tuple[str, str, float, float]]


def _score(equity: pd.Series) -> float:
    """Return/mdd hybrid: CAGR / |maxDD|, guarded."""
    if equity.empty or len(equity) < 30:
        return 0.0
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    total = equity.iloc[-1] / equity.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else 0.0
    dd = (equity / equity.cummax() - 1).min()
    return float(cagr / abs(dd)) if dd < -1e-9 else 0.0


def walk_forward(
    df: pd.DataFrame,
    strategy_factory,
    train_years: float = 3.0,
    test_years: float = 1.0,
    step_years: float = 1.0,
    bt: Backtester | None = None,
) -> WFResult:
    bt = bt or Backtester()
    end = df.index[-1]
    ins_list, oos_list, windows = [], [], []
    cur = df.index[0]
    while (end - cur).days / 365.25 >= train_years + 0.5:
        tr_end = cur + pd.DateOffset(years=int(train_years))
        te_end = min(tr_end + pd.DateOffset(years=int(test_years)), end)
        tr = df[df.index < tr_end]
        te = df[(df.index >= tr_end) & (df.index < te_end)]
        if len(tr) > 250 and len(te) > 100:
            ins = _score(bt.run(tr, strategy_factory()).equity)
            oos = _score(bt.run(te, strategy_factory()).equity)
            ins_list.append(ins)
            oos_list.append(oos)
            windows.append((str(cur.date()), str(te_end.date()), round(ins, 3), round(oos, 3)))
        cur = cur + pd.DateOffset(years=int(step_years))
    ins = float(np.mean(ins_list)) if ins_list else 0.0
    oos = float(np.mean(oos_list)) if oos_list else 0.0
    ratio = oos / ins if ins > 0 else 0.0
    return WFResult(round(ins, 4), round(oos, 4), round(ratio, 3), windows)


def parameter_plateau(run_one, grid: list[dict]) -> dict:
    """run_one(params)->score. Returns spread stats across the grid — pass only if the
    plateau is broadly positive, not a single sharp peak."""
    scores = {tuple(sorted(p.items())): run_one(p) for p in grid}
    vals = np.array(list(scores.values()))
    return {
        "n": len(vals),
        "best": float(vals.max()),
        "median": float(np.median(vals)),
        "worst": float(vals.min()),
        "frac_positive": float((vals > 0).mean()),
        "detail": {str(k): round(float(v), 3) for k, v in scores.items()},
    }
