import pytest

from indtrader.broker.fyers import Holding, parse_fyers_holdings
from indtrader.broker.reconcile import reconcile
from indtrader.execution.planlog import PlanLog
from indtrader.models import OrderPlan

FYERS_OK = {
    "s": "ok",
    "d": {
        "net": [
            {"symbol": "NSE:RELIANCE-EQ", "netQty": 100, "netAvgPrice": 1400.5, "ltp": 1450},
            {"symbol": "NSE:TCS-EQ", "netQty": 0, "netAvgPrice": 300, "ltp": 310},   # zero qty -> skip
            {"symbol": "NSE:ITC-EQ", "netQty": 500, "netAvgPrice": 420, "ltp": 435},
            {"symbol": "NSE:BAJAJ-AUTO-EQ", "netQty": 5, "netAvgPrice": 7000, "ltp": 7500},
            {"symbol": "BROKEN", "netQty": "abc"},                                    # dropped
        ]
    },
}


def test_parse_fyers_holdings():
    hs = parse_fyers_holdings(FYERS_OK)
    assert [h.symbol for h in hs] == ["RELIANCE", "ITC", "BAJAJ-AUTO"]  # zero-qty/broken dropped
    assert hs[0] == Holding("RELIANCE", 100, 1400.5, 1450.0)


def test_parse_fyers_error_envelope_raises():
    with pytest.raises(RuntimeError, match="fyers error"):
        parse_fyers_holdings({"s": "error", "message": "invalid token"})


def test_journal_executed_positions(tmp_path):
    log = PlanLog(path=tmp_path / "plans.sqlite")
    log.add_plan(OrderPlan("reliance", "BUY", 100, 1400, 1300, 1600, "donchian breakout", "breakout"))
    log.add_plan(OrderPlan("TCS", "BUY", 50, 300, 280, 340, "trend", "pullback"))
    log.add_plan(OrderPlan("TCS", "SELL", 50, 340, 280, 340, "target hit", ""))
    for pid in (1, 2, 3):
        log.mark(pid, "executed")
    log.add_plan(OrderPlan("INFY", "BUY", 10, 1500, 1400, 1700, "never executed", ""))
    pos = log.executed_positions()
    log.close()
    assert pos == {"RELIANCE": 100}  # TCS netted to zero, INFY still open


def _h(sym, qty):
    return Holding(sym, qty, 100.0, 100.0)


def test_reconcile_all_three_issue_types():
    journal = {"A": 100, "B": 200, "C": 50}
    broker = [_h("A", 100), _h("B", 150), _h("D", 10)]
    rep = reconcile(journal, broker)
    assert rep.missing_at_broker == ["C"]
    assert rep.unjournalled == ["D"]
    assert rep.qty_mismatch == [("B", 200, 150)]
    assert rep.has_issues
    text = "\n".join(rep.lines())
    assert "MISSING AT BROKER: C" in text and "UNJOURNALLED: D" in text and "QTY MISMATCH: B" in text


def test_reconcile_clean():
    rep = reconcile({"A": 10}, [_h("A", 10)])
    assert not rep.has_issues
    assert "match" in rep.lines()[-1]
