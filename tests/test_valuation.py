from datetime import date

from indtrader.portfolio.tax import LedgerEntry
from indtrader.portfolio.valuation import book_xirr, book_flows, held_quantities

FILLS = [
    LedgerEntry(date(2025, 1, 2), "TATA", "BUY", 100, 100.0, 50.0),
    LedgerEntry(date(2025, 6, 2), "TATA", "SELL", 50, 120.0, 40.0),
]


def test_flows_sign_convention_and_holdings():
    fl = book_flows(FILLS)
    assert fl[0] == (date(2025, 1, 2), -10050.0)   # outflow incl costs
    assert fl[1] == (date(2025, 6, 2), 5960.0)     # inflow net costs
    assert held_quantities(FILLS) == {"TATA": 50}


def test_xirr_computes_with_marks():
    out = book_xirr(FILLS, {"TATA": 130.0}, asof=date(2026, 1, 5))
    assert out["ok"] and out["xirr"] is not None
    assert out["open_value"] == 6500.0
    assert out["invested"] == 10050.0 and out["realized_inflow"] == 5960.0


def test_missing_mark_refuses_not_guesses():
    out = book_xirr(FILLS, {"TATA": None})
    assert not out["ok"] and out["missing_marks"] == ["TATA"]


def test_single_fill_is_not_a_return():
    out = book_xirr(FILLS[:1], {"TATA": 130.0})
    assert not out["ok"] and "fewer than 2 fills" in out["reason"]
