from datetime import date

import pytest

from indtrader.portfolio.limits import RiskBlocked, RiskManager


def test_kill_switch_blocks_everything():
    rm = RiskManager(kill_switch=True, mode="live")
    with pytest.raises(RiskBlocked, match="KILL-SWITCH"):
        rm.check_new_position("RELIANCE", {}, 0.0)


def test_daily_loss_limit_live_only():
    rm = RiskManager(mode="live")
    with pytest.raises(RiskBlocked, match="daily loss"):
        rm.check_new_position("TCS", {}, -25_000.0)
    # paper mode: advisory system still works while we learn — limit not enforced
    rm2 = RiskManager(mode="paper")
    rm2.check_new_position("TCS", {}, -25_000.0)


def test_per_symbol_and_position_caps():
    rm = RiskManager(mode="live", max_per_symbol=1, max_positions=2)
    with pytest.raises(RiskBlocked, match="already holding"):
        rm.check_new_position("INFY", {"INFY": 1}, 0.0)
    with pytest.raises(RiskBlocked, match="open-positions cap"):
        rm.check_new_position("INFY", {"A": 1, "B": 1}, 0.0)


def test_no_stale_data():
    with pytest.raises(RiskBlocked, match="stale"):
        RiskManager.no_stale_data(date(2026, 1, 5), date(2026, 3, 1))
    RiskManager.no_stale_data(date(2026, 1, 9), date(2026, 1, 12), max_stale_days=3)  # weekend ok
