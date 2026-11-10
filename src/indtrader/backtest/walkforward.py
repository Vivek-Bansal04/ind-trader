"""Walk-forward + parameter-plateau validation — the gate a strategy must pass
before it is trusted with money (build-plan Phase 2 / standing rule).

Contract:
- fold i: model is CHOSEN on the train window (in-sample), executed only on the
  following test window (out-of-sample). Windows never overlap, folds never shuffle.
- IS/OOS metric per fold; overfit ratio = OOS/IS (only meaningful when IS > 0).
- plateau: every grid point within +-pct of the chosen parameter must not fall off
  a cliff — a strategy that works at exactly one setting is a fit, not an edge.
- every number is net of DeliveryCosts and executes at next-bar-open (engine rules).
- honest failure: too few bars / no trades in OOS => ratio None => gate FAILS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Callable

import numpy as np
import pandas as pd

from indtrader.backtest.costs import DeliveryCosts
from indtrader.backtest.engine import Backtester
from indtrader.backtest.strategies import Strategy

METRICS = {"sharpe", "cagr", "profit_factor"}


def grid_candidates(cls: type[Strategy], grid: dict[str, list], max_grid: int = 12) -> list[dict]:
    keys, vals = zip(*sorted(grid.items()))
    combos = [dict(zip(keys, v)) for v in product(*vals)]
    if len(combos) > max_grid:
        raise ValueError(f"grid too large ({len(combos)} combos, cap {max_grid}) — narrow it")
    return combos


def run_window(df: pd.DataFrame, cls: type[Strategy], params: dict, costs: DeliveryCosts,
               metric: str = "sharpe", capital: float = 1_000_000.0) -> tuple[float, int]:
    res = Backtester(costs=costs, capital=capital).run(df, cls(**params))
    s = res.summary()
    if s.get("trades", 0) < 1 or metric not in s or s.get(metric) is None:
        return float("nan"), int(s.get("trades", 0))
    return float(s[metric]), int(s["trades"])


@dataclass
class GateReport:
    strategy: str
    metric: str
    folds: list[dict]
    plateau: dict
    chosen_by_fold: list[dict]
    passed: bool
    reasons: list[str] = field(default_factory=list)

    def as_rows(self) -> list[dict]:
        rows = [{**f, "kind": "fold"} for f in self.folds]
        rows += [{"kind": "plateau", **k} for k in self.plateau.values()]
        return rows


def walk_forward_gate(df: pd.DataFrame, cls: type[Strategy], grid: dict[str, list],
                      costs: DeliveryCosts | None = None, metric: str = "sharpe",
                      warmup: int = 400, test_days: int = 252, min_is: float = 0.3,
                      min_ratio: float = 0.5, min_plateau: float = 0.5,
                      plateau_tol: float = 0.35, capital: float = 1_000_000.0,
                      progress: Callable[[str], None] | None = None) -> GateReport:
    """`df` needs at least warmup + 2*test_days bars for one fold to mean anything."""
    costs = costs or DeliveryCosts()
    n = len(df)
    reasons: list[str] = []
    folds: list[dict] = []
    chosen_by_fold: list[dict] = []
    starts = list(range(0, n - warmup - test_days + 1, test_days))
    if len(starts) < 2:
        return GateReport(cls.name, metric, [], {}, [], False,
                          [f"not enough bars: {n} (need >= {warmup + 2 * test_days})"])
    for s0 in starts:
        tr = df.iloc[s0:s0 + warmup]
        te = df.iloc[s0 + warmup:s0 + warmup + test_days]
        scores = []
        for params in grid_candidates(cls, grid):
            m, _ = run_window(tr, cls, params, costs, metric, capital)
            scores.append((params, m))
        valid = [(p, m) for p, m in scores if not np.isnan(m)]
        if not valid:
            folds.append({"window": f"{tr.index[0].date()}..{te.index[-1].date()}",
                          "is": float("nan"), "oos": float("nan"), "ratio": None,
                          "chosen": "n/a", "oos_trades": 0})
            reasons.append("no parameter produced trades in the IS window")
            continue
        best_p, best_m = max(valid, key=lambda x: x[1])
        oos, oos_tr = run_window(te, cls, best_p, costs, metric, capital)
        ratio = (oos / best_m) if (best_m > 0 and not np.isnan(oos)) else None
        folds.append({"window": f"{tr.index[0].date()}..{te.index[-1].date()}",
                      "is": round(best_m, 3), "oos": round(oos, 3) if not np.isnan(oos) else None,
                      "ratio": round(ratio, 2) if ratio is not None else None,
                      "chosen": str(best_p), "oos_trades": oos_tr})
        chosen_by_fold.append({**best_p, "count": 1})
    if progress:
        progress(f"{cls.name}: {len(folds)} folds evaluated")

    # --- plateau on the FULL history, at the modal fold choice ---
    from collections import Counter

    votes = Counter(tuple(sorted({k: v for k, v in c.items() if k != "count"}.items()))
                    for c in chosen_by_fold)
    modal_params = dict(votes.most_common(1)[0][0]) if votes else grid_candidates(cls, grid)[0]
    plateau: dict[str, dict] = {}
    for params in grid_candidates(cls, grid):
        m, _ = run_window(df, cls, params, costs, metric, capital)
        key = str(params)
        plateau[key] = {"params": key, "full_metric": None if np.isnan(m) else round(m, 3)}
    chosen_m = plateau[str(modal_params)]["full_metric"]
    neighbours = {k: v["full_metric"] for k, v in plateau.items() if k != str(modal_params)}
    if chosen_m is None or chosen_m <= 0:
        reasons.append(f"modal choice is not profitable net of costs on full history ({chosen_m})")
        ok_plateau = False
    else:
        good = sum(1 for v in neighbours.values() if v is not None and v >= (1 - plateau_tol) * chosen_m)
        ok_plateau = good >= min_plateau * len(neighbours) if neighbours else False
        if not ok_plateau:
            reasons.append(f"plateau thin: {good}/{len(neighbours)} neighbours within "
                           f"{int((1 - plateau_tol) * 100)}% of the chosen setting")
    valid_ratios = [f["ratio"] for f in folds if f["ratio"] is not None]
    if not valid_ratios:
        reasons.append("no fold produced a computable OOS/IS ratio")
    else:
        mean_ratio = float(np.mean(valid_ratios))
        if mean_ratio < min_ratio:
            reasons.append(f"mean OOS/IS ratio {mean_ratio:.2f} < {min_ratio}")
    if any(np.isnan(f["is"]) or f["is"] < min_is for f in folds):
        reasons.append(f"at least one fold IS metric below min {min_is} (or uncomputable)")
    passed = ok_plateau and bool(valid_ratios) and float(np.mean(valid_ratios)) >= min_ratio \
        and not any(np.isnan(f["is"]) or f["is"] < min_is for f in folds)
    return GateReport(cls.name, metric, folds, plateau, chosen_by_fold, passed, reasons)
