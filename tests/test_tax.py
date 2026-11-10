from datetime import date

import pytest

from indtrader.portfolio.tax import LedgerEntry, fy_of, months_between, realize, tax_summary

B = lambda d, q, p, c=0.0, s="X": LedgerEntry(d, s, "BUY", q, p, c)  # noqa: E731
S = lambda d, q, p, c=0.0, s="X": LedgerEntry(d, s, "SELL", q, p, c)  # noqa: E731


def test_months_and_fy_boundaries():
    assert months_between(date(2024, 1, 31), date(2025, 1, 30)) == 11  # day short -> 11
    assert months_between(date(2024, 1, 31), date(2025, 1, 31)) == 12
    assert fy_of(date(2024, 3, 31)) == "FY2023-24"
    assert fy_of(date(2024, 4, 1)) == "FY2024-25"


def test_fifo_order_and_buckets():
    # buy 100 @100 (Jan-23), buy 100 @200 (Jan-24); sell 150 @300 (Feb-24)
    m = realize([B(date(2023, 1, 10), 100, 100), B(date(2024, 1, 10), 100, 200),
                 S(date(2024, 2, 10), 150, 300)])
    assert len(m) == 2
    lt, st = m[0], m[1]  # FIFO: old lot first
    assert lt.bucket == "LTCG" and lt.qty == 100 and lt.gross == 20000
    assert st.bucket == "STCG" and st.qty == 50 and st.gross == 5000


def test_costs_prorated_both_sides():
    m = realize([B(date(2026, 1, 5), 100, 100, c=10.0), S(date(2026, 3, 5), 50, 120, c=8.0)])
    assert m[0].costs == pytest.approx(5.0 + 8.0)  # half buy costs; sell costs belong wholly to the sold qty
    assert m[0].net == pytest.approx(1000 - 13.0)


def test_oversell_raises():
    with pytest.raises(ValueError, match="exceeds holdings"):
        realize([B(date(2026, 1, 5), 10, 100), S(date(2026, 2, 5), 20, 110)])


def test_ltcg_exempt_125k_then_taxed():
    entries = [
        B(date(2020, 1, 1), 1000, 100, s="A"),
        B(date(2020, 2, 1), 1000, 100, s="B"),               # two big old LTCG bases
        S(date(2025, 6, 1), 1000, 220, c=5000, s="A"),       # LTCG gross 120k -> exempt
        S(date(2025, 7, 1), 1000, 300, c=5000, s="B"),       # LTCG gross 200k -> 75k after exemption
        B(date(2025, 1, 1), 100, 500, s="C"),
        S(date(2025, 8, 1), 100, 600, c=1000, s="C"),        # STCG (600-500)*100 - 1000 = 9k
    ]
    [fy] = tax_summary(realize(entries))
    assert fy.financial_year == "FY2025-26"
    assert fy.ltcg_net == pytest.approx(120000 - 5000 + 200000 - 5000)  # 310000
    assert fy.ltcg_taxable == pytest.approx(310000 - 125000)            # 185000
    assert fy.stcg_net == pytest.approx(9000)
    assert fy.tax_stcg == pytest.approx(1800)
    assert fy.tax_ltcg == pytest.approx(23125)
    assert fy.tax_total == pytest.approx(24925)


def test_negative_ltcg_gets_no_negative_tax():
    entries = [B(date(2020, 1, 1), 100, 500, s="A"), S(date(2025, 5, 1), 100, 300, s="A")]
    [fy] = tax_summary(realize(entries))
    assert fy.ltcg_net < 0 and fy.ltcg_taxable == 0 and fy.tax_ltcg == 0


def test_journal_roundtrip(tmp_path):
    from indtrader.execution.planlog import PlanLog

    log = PlanLog(path=tmp_path / "plans.sqlite")
    log.add_fill("2026-01-05", "reliance", "BUY", 100, 1400, 150, "donchian entry")
    log.add_fill("2026-06-01", "RELIANCE", "SELL", 100, 1600, 170, "stopped out late")
    fills = log.fills()
    log.close()
    assert [f.side for f in fills] == ["BUY", "SELL"]
    m = realize(fills)
    assert m[0].bucket == "STCG" and m[0].net == pytest.approx(20000 - 320)
