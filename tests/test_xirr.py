from datetime import date

import pytest

from indtrader.portfolio.xirr import xirr


def test_xirr_golden():
    # -100000 now, +60000 after 1y, +60000 after 2y  => IRR ~13.06%
    flows = [(date(2024, 1, 1), -100_000.0), (date(2025, 1, 1), 60_000.0), (date(2026, 1, 1), 60_000.0)]
    assert xirr(flows) == pytest.approx(0.1306, abs=0.002)


def test_xirr_double_money_in_two_years():
    flows = [(date(2020, 1, 1), -100.0), (date(2022, 1, 1), 200.0)]
    assert xirr(flows) == pytest.approx(0.4142, abs=0.002)  # sqrt(2)-1


def test_xirr_no_sign_change_returns_none():
    flows = [(date(2024, 1, 1), -100.0), (date(2025, 1, 1), -50.0)]
    assert xirr(flows) is None


def test_xirr_irregular_dates():
    flows = [
        (date(2022, 3, 5), -50_000.0),
        (date(2022, 8, 20), -25_000.0),
        (date(2023, 5, 1), 12_000.0),
        (date(2024, 11, 11), 80_000.0),
    ]
    r = xirr(flows)
    assert r is not None and -0.9 < r < 5.0
