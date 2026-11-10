import pytest

from indtrader.backtest.costs import DeliveryCosts, buy_qty_for


def test_round_trip_on_lakh_notional():
    c = DeliveryCosts()
    rt = c.round_trip_fraction(100_000)
    # stt 0.2% both sides + stamp 0.015 + txn/gst/slbm crumbs -> expect ~0.22-0.25%
    assert 0.0020 < rt < 0.0030


def test_discount_broker_delivery_has_zero_brokerage():
    c = DeliveryCosts()
    assert c.buy_costs(500_000) < 500_000 * 0.002  # delivery CNC: no brokerage
    assert c._brokerage(500_000) == 0.0


def test_percentage_broker_capped():
    c = DeliveryCosts(brokerage_rate=0.0005, brokerage_cap=20)
    assert c._brokerage(10_000_000) == 20.0


def test_buy_qty_never_overdrafts():
    c = DeliveryCosts(brokerage_rate=0.0003, brokerage_cap=20)
    cash, price = 100_000, 333.0
    q = buy_qty_for(cash, price, c)
    assert q >= 1
    assert q * price + c.buy_costs(q * price) <= cash
    assert (q + 1) * price + c.buy_costs((q + 1) * price) > cash
