"""Real XIRR (money-weighted return). mftool's 'IRR_annualised_return' was a CAGR —
this is the correct bisection solver over actual dated flows."""
from __future__ import annotations

from datetime import date

DAY0 = 365.0


def xnpv(rate: float, flows: list[tuple[date, float]]) -> float:
    if not flows:
        return 0.0
    t0 = min(d for d, _ in flows)
    return sum(cf / (1.0 + rate) ** ((d - t0).days / DAY0) for d, cf in flows)


def xirr(flows: list[tuple[date, float]], lo: float = -0.9999, hi: float = 10.0, tol: float = 1e-7, iters: int = 200) -> float | None:
    """Annualized IRR for irregular dated cashflows (negative=invest, positive=receive).
    Returns None when no sign change or no root in [lo, hi]."""
    if len(flows) < 2:
        return None
    has_pos = any(cf > 0 for _, cf in flows)
    has_neg = any(cf < 0 for _, cf in flows)
    if not (has_pos and has_neg):
        return None
    flo, fhi = xnpv(lo, flows), xnpv(hi, flows)
    if flo * fhi > 0:  # no bracket — expand hi once, else give up honestly
        hi *= 4
        fhi = xnpv(hi, flows)
        if flo * fhi > 0:
            return None
    for _ in range(iters):
        mid = (lo + hi) / 2
        fmid = xnpv(mid, flows)
        if abs(fmid) < tol or (hi - lo) / 2 < tol:
            return mid
        if flo * fmid < 0:
            hi, fhi = mid, fmid
        else:
            lo, flo = mid, fmid
    return (lo + hi) / 2
