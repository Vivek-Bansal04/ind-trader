"""Dividend / corporate-action ledger (plan Phase 6: "dividend/corporate-action ledger").

Manual-entry by design — the human transcribes what the broker/fund statement SHOWED
(e.g. "dividend ₹4,000 credited", "2,000 bonus units received", "3,000 redeemed, ₹4,500
TDS deducted"). The engine never infers corporate actions from price jumps and never
guesses a payout: wrong inferred cashflows poison XIRR and the tax sheet worse than a
missing one.

Cost-basis treatment (documented, standard, not advice):
- bonus: zero-cost units added on the ex-date (basis unchanged, holding start = that date);
- split: every BUY before the action rescales qty * r, price / r — FIFO stays consistent
  because the SELLs that follow match against the rescaled lots;
- redemption / dividend: cash inflow on the payout date; a redemption also synthesizes
  its own SELL at gross/units (TDS as costs) — record ONLY the action, not a fill too
  (double-entry would double-count the exit).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

KINDS = ("dividend", "bonus", "split", "redemption")


@dataclass(frozen=True)
class CorpAction:
    dt: date
    symbol: str
    kind: str
    qty_delta: float   # +bonus units, +split delta, -redemption units (0 for dividend)
    cash: float        # net received (0 for bonus/split)
    tds: float         # TDS withheld on that cash (0 if none)
    note: str = ""


def load_actions(log) -> list[CorpAction]:
    log.con.executescript(ACTIONS_SCHEMA)
    rows = log.con.execute(
        "SELECT dt,symbol,kind,qty_delta,cash,tds,note FROM actions ORDER BY dt, id"
    ).fetchall()
    return [CorpAction(date.fromisoformat(str(r[0])[:10]), r[1], r[2], float(r[3]),
                       float(r[4]), float(r[5]), r[6]) for r in rows]


ACTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dt TEXT, symbol TEXT, kind TEXT,      -- dividend | bonus | split | redemption
    qty_delta REAL DEFAULT 0, cash REAL DEFAULT 0, tds REAL DEFAULT 0, note TEXT
);
"""


def add_action(log, dt: date, symbol: str, kind: str, qty_delta: float = 0.0,
               cash: float = 0.0, tds: float = 0.0, note: str = "") -> int:
    kind = kind.lower()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if qty_delta < 0 or cash < 0 or tds < 0:
        raise ValueError("qty_delta/cash/tds must be >= 0 (sell/instalment flows belong in the fills ledger)")
    if kind == "bonus" and qty_delta <= 0:
        raise ValueError("bonus needs a positive qty_delta (units received)")
    if kind == "split" and qty_delta <= 0:
        raise ValueError("split needs qty_delta = units ADDED by the split (e.g. 1:2 on 100 -> 100)")
    if kind in ("dividend", "redemption") and cash <= 0:
        raise ValueError(f"{kind} needs the cash actually received")
    if kind == "redemption" and qty_delta <= 0:
        raise ValueError("redemption needs qty_delta = units redeemed "
                         "(the SELL is synthesized from this action — do NOT record a fill too)")
    log.con.executescript(ACTIONS_SCHEMA)
    cur = log.con.execute(
        "INSERT INTO actions(dt,symbol,kind,qty_delta,cash,tds,note) VALUES (?,?,?,?,?,?,?)",
        (str(dt), symbol.upper(), kind, float(qty_delta), float(cash), float(tds), note),
    )
    log.con.commit()
    return int(cur.lastrowid)


def apply_actions(fills: list, actions: list[CorpAction]) -> list:
    """Fill list -> cost-basis-corrected fill list (bonus zero-cost lots, split
    rescaling). Idempotent only in the sense of being a pure function — call once
    per computation, never store the result back."""
    from indtrader.portfolio.tax import LedgerEntry

    out = list(fills)
    for a in actions:
        if a.kind == "bonus":
            out.append(LedgerEntry(a.dt, a.symbol, "BUY", a.qty_delta, 0.0, 0.0))
        elif a.kind == "split":
            out = _rescale_buys(out, a)
        elif a.kind == "redemption":
            out.append(LedgerEntry(a.dt, a.symbol, "SELL", a.qty_delta,
                                   round((a.cash + a.tds) / a.qty_delta, 6) if a.qty_delta else 0.0,
                                   a.tds))
    return sorted(out, key=lambda f: (f.dt, f.side.upper() == "BUY"))


def _rescale_buys(fills: list, a: CorpAction) -> list:
    """Split rescaling done right: total PRE-split bought units for the symbol are
    scaled by the ratio implied by qty_delta, keeping total cost identical."""
    from indtrader.portfolio.tax import LedgerEntry

    pre = [f for f in fills if f.symbol.upper() == a.symbol and f.side.upper() == "BUY"
           and f.dt < a.dt]
    base = sum(f.qty for f in pre)
    if base <= 0:
        return fills  # split action with no prior buys — leave untouched, tax engine will complain loudly
    ratio = (base + a.qty_delta) / base
    out = []
    for f in fills:
        if f in pre:
            out.append(LedgerEntry(f.dt, f.symbol, f.side, f.qty * ratio, f.price / ratio, f.costs))
        else:
            out.append(f)
    return out


def extra_flows(actions: list[CorpAction]) -> list[tuple[date, float]]:
    """Cash in on payout dates. Redemption cash is NOT added here — it enters as a
    SELL fill via the ledger (avoid double-counting)."""
    return [(a.dt, a.cash) for a in actions if a.kind == "dividend" and a.cash > 0]


def dividend_income_by_fy(actions: list[CorpAction]) -> dict[str, dict]:
    """Per-FY gross + TDS + net for the dividend-income section of the tax report.
    (Taxed at slab rates — this sheet reports, it does not compute income tax.)"""
    from indtrader.portfolio.tax import fy_of

    out: dict[str, dict] = {}
    for a in actions:
        if a.kind != "dividend" or a.cash <= 0:
            continue
        d = out.setdefault(fy_of(a.dt), {"gross": 0.0, "tds": 0.0, "net": 0.0, "n": 0})
        d["gross"] += a.cash + a.tds
        d["tds"] += a.tds
        d["net"] += a.cash
        d["n"] += 1
    return {fy: {k: round(v, 2) for k, v in d.items()} for fy, d in sorted(out.items())}


def recent_income(actions: list[CorpAction], days: int = 30, today: date | None = None) -> dict:
    """Rolling-window income summary for review/riskreport/bot."""
    from datetime import timedelta

    today = today or date.today()
    since = today - timedelta(days=days)
    cash = sum(a.cash for a in actions if a.kind in ("dividend", "redemption") and a.dt > since)
    tds = sum(a.tds for a in actions if a.kind in ("dividend", "redemption") and a.dt > since)
    acts = [a for a in actions if a.dt > since]
    return {"days": days, "cash_in": round(cash, 2), "tds": round(tds, 2), "events": len(acts)}
