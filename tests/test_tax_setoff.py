from datetime import date

from indtrader.portfolio.tax import (
    LTCG_EXEMPTION,
    RealizedMatch,
    fy_of,
    tax_summary_with_setoff,
)


def m(bucket: str, sell: date, net: float) -> RealizedMatch:
    return RealizedMatch("SYM", date(2020, 1, 1), sell, 100, net, 0.0, bucket)


def rows(matches):
    return {r.financial_year: r for r in tax_summary_with_setoff(matches)}


def test_fy_of():
    assert fy_of(date(2024, 3, 31)) == "FY2023-24"
    assert fy_of(date(2024, 4, 1)) == "FY2024-25"


def test_chronological_setoff_and_carry():
    r = rows([
        m("STCG", date(2023, 8, 1), -100_000),   # FY23-24: STCL absorbs 100k of the 150k LTCG
        m("LTCG", date(2024, 1, 5), +150_000),   #          -> 50k LTCG, fully under exemption
        m("STCG", date(2024, 5, 2), +80_000),    # FY24-25: clean 20% year
        m("STCG", date(2025, 6, 1), -30_000),    # FY25-26: loss carried
        m("LTCG", date(2026, 7, 1), +200_000),   # FY26-27: carried STCL absorbs LTCG (statutory)
    ])
    a = r["FY2023-24"]
    assert (a.taxable_stcg, a.taxable_ltcg, a.tax_total) == (0.0, 0.0, 0.0)
    assert a.exemption_used == 50_000
    b = r["FY2024-25"]
    assert (b.taxable_stcg, b.tax_total) == (80_000, 16_000)
    c = r["FY2025-26"]
    assert (c.tax_total, c.carry_stcg_out) == (0.0, 30_000)
    d = r["FY2026-27"]
    assert d.used_brought_stcl == 30_000
    assert d.taxable_ltcg == round(170_000 - LTCG_EXEMPTION, 2)
    assert d.tax_total == round(45_000 * 0.125, 2)


def test_stcl_absorbs_ltcg_but_never_the_reverse():
    r = rows([
        m("LTCG", date(2024, 5, 1), -50_000),   # FY24-25: LTCL cannot touch the STCG gain
        m("STCG", date(2024, 9, 1), +40_000),
        m("LTCG", date(2025, 5, 1), +60_000),   # FY25-26: LTCL absorbed; 10k < exemption
    ])
    a = r["FY2024-25"]
    assert (a.taxable_stcg, a.tax_total, a.carry_ltcg_out) == (40_000, 8_000, 50_000)
    b = r["FY2025-26"]
    assert b.used_brought_ltlg == 50_000
    assert (b.taxable_ltcg, b.tax_total, b.carry_ltcg_out) == (0.0, 0.0, 0.0)


def test_eight_year_expiry_and_gap_years_still_age():
    r = rows([
        m("STCG", date(2015, 8, 1), -100_000),  # FY2015-16 loss
        m("STCG", date(2022, 8, 1), +50_000),   # usable (age 7 < 8): tax on net 50k
        m("STCG", date(2023, 8, 1), +50_000),   # expired at start of this FY (age 8)
    ])
    a = r["FY2015-16"]
    assert (a.tax_total, a.carry_stcg_out) == (0.0, 100_000)
    b = r["FY2022-23"]
    assert (b.used_brought_stcl, b.tax_total) == (50_000, 0.0)  # absorbed fully — tax zero that year
    assert "expired" in b.note and "50,000" in b.note  # remainder hit the 8-year wall here
    c = r["FY2023-24"]
    # without calendar-gap aging the remainder would still be alive here (age 3 of P&L rows)
    # and would absorb this year too; instead this year pays full tax — the aging is real
    assert c.used_brought_stcl == 0.0 and c.tax_total == 10_000
    # the 50k that survived the 6-year gap (2016-17..2021-22, no P&L years) proves
    # carry ages by CALENDAR years, not by count of P&L rows


def test_no_matches_is_nothing_not_zero_history():
    assert tax_summary_with_setoff([]) == []
