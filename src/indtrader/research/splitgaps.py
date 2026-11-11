"""Split-gap arithmetic — ONE implementation shared by the `health` gate, the
`coractions --gap-list` batch and the dashboard's split-gap ledger. Three
callers, one function: the page, the gate and the batch can never disagree.

F = adj/raw back-adjustment (store.combined_factors: yfinance ∪ recorded NSE
splits). A 1:2 split halves closes at day d and steps F by a split-sized
factor there; a step of ~1.0 (dividend-only) leaves the cliff in the ADJUSTED
series — the round-4 HDFCBANK lie this check exists to catch."""
from __future__ import annotations

SPLIT_JUMP = 0.35  # raw one-day move this big is split/bonus until proven otherwise


def symbol_split_gaps(store, sym: str, df, jump: float = SPLIT_JUMP) -> list[str]:
    """Every |raw move| >= jump that NO merged-factor step of split size explains
    on that day. Returns ['date +x%', ...] (factor step appended when one exists
    but is the wrong size). Empty list = every cliff is accounted for."""
    import pandas as pd

    fc = store.combined_factors(sym)
    gaps = []
    pct = df["close"].pct_change()
    for ts, p in pct[pct.abs() >= jump].items():
        d = pd.Timestamp(ts)
        step = None
        if len(fc):
            before = fc[fc.index < d]
            on = fc[fc.index <= d]
            if len(on):  # no factor at/before d at all -> step None -> gap
                b = float(before.iloc[-1]) if len(before) else 1.0
                step = round(float(on.iloc[-1]) / b, 3) if b else None
        covered = step is not None and (0.4 <= step <= 0.7 or 1.4 <= step <= 2.5)
        if not covered:
            gaps.append(f"{d.date()} {p:+.0%}" + (f" (factor step {step})" if step else ""))
    return gaps
