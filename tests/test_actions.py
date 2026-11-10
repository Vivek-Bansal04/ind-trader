from datetime import date

import pytest

from indtrader.execution.planlog import PlanLog
from indtrader.portfolio import actions as A
from indtrader.portfolio.actions import CorpAction, add_action, apply_actions
from indtrader.portfolio.tax import LedgerEntry, realize
from indtrader.portfolio.valuation import book_flows, book_xirr, held_quantities

BUY = LedgerEntry(date(2024, 1, 1), "TATA", "BUY", 100, 200.0, 0.0)


def split(dt, delta, sym="TATA"):
    return CorpAction(dt, sym, "split", delta, 0.0, 0.0)


# ---- validation ladder ---------------------------------------------------------

def test_add_action_validates_loudly(tmp_path):
    log = PlanLog(tmp_path / "p.sqlite")
    with pytest.raises(ValueError, match="kind must be"):
        add_action(log, date(2026, 1, 1), "TATA", "merger")
    with pytest.raises(ValueError, match="bonus needs"):
        add_action(log, date(2026, 1, 1), "TATA", "bonus", qty_delta=0)
    with pytest.raises(ValueError, match="dividend needs"):
        add_action(log, date(2026, 1, 1), "TATA", "dividend", cash=0)
    with pytest.raises(ValueError, match=">= 0"):
        add_action(log, date(2026, 1, 1), "TATA", "dividend", cash=-5)
    assert add_action(log, date(2026, 8, 1), "TATA", "dividend", cash=4000, tds=200) == 1
    log.close()


# ---- cost-basis correctness ------------------------------------------------------

def test_bonus_adds_zero_cost_units_not_free_money():
    out = apply_actions([BUY], [CorpAction(date(2024, 6, 1), "TATA", "bonus", 100, 0, 0)])
    assert held_quantities(out) == {"TATA": 200}
    sells = out + [LedgerEntry(date(2024, 8, 1), "TATA", "SELL", 200, 250.0, 0.0)]
    matches = realize(sells)                       # would raise "exceeds holdings" without the bonus
    assert sum(m.qty for m in matches) == 200
    # original lot: (250-200)*100 = 5,000; bonus lot: (250-0)*100 = 25,000 -> 30,000 total
    assert sum(m.gross for m in matches) == pytest.approx(30_000)


def test_split_preserves_total_cost_so_pnl_is_unchanged():
    out = apply_actions([BUY], [split(date(2024, 6, 1), 100)])   # 1:2 -> +100 units
    h = held_quantities(out)
    assert h == {"TATA": 200}
    buys = [f for f in out if f.side == "BUY"]
    assert buys[0].qty == 200 and buys[0].price == pytest.approx(100.0)
    # sell everything at the split-adjusted price -> zero P&L (it was pure re-labelling)
    sells = out + [LedgerEntry(date(2024, 8, 1), "TATA", "SELL", 200, 100.0, 0.0)]
    assert sum(m.gross for m in realize(sells)) == pytest.approx(0.0)


def test_redemption_synthesizes_the_sell_once():
    mf_buy = LedgerEntry(date(2024, 1, 1), "MF:100001", "BUY", 100, 50.0, 0.0)
    acts = [CorpAction(date(2024, 8, 1), "MF:100001", "redemption", 100, 6000.0, 500.0)]
    out = apply_actions([mf_buy], acts)
    assert held_quantities(out) == {}
    m = realize(out)[0]
    assert m.gross == pytest.approx(100 * (65.0 - 50.0))         # gross/units = 65
    assert m.costs == pytest.approx(500.0)                        # TDS rides as cost


# ---- flows + income ---------------------------------------------------------------

def test_dividend_flows_land_in_xirr_once():
    fills = [LedgerEntry(date(2025, 1, 1), "TATA", "BUY", 100, 100.0, 0.0),
             LedgerEntry(date(2025, 6, 1), "TATA", "SELL", 100, 110.0, 0.0)]
    acts = [CorpAction(date(2025, 4, 1), "TATA", "dividend", 0, 400.0, 20.0)]
    plain = book_xirr(fills, {}, asof=date(2025, 12, 31))
    with_div = book_xirr(fills, {}, asof=date(2025, 12, 31), extra_flows=A.extra_flows(acts))
    assert plain["ok"] and with_div["ok"]
    assert with_div["realized_inflow"] == plain["realized_inflow"] + 400.0
    assert A.extra_flows(acts) == [(date(2025, 4, 1), 400.0)]


def test_dividend_income_by_fy_separates_gross_tds_net():
    acts = [CorpAction(date(2025, 5, 1), "TATA", "dividend", 0, 3000.0, 150.0),
            CorpAction(date(2025, 8, 1), "TATA", "dividend", 0, 1000.0, 50.0),
            CorpAction(date(2026, 5, 1), "TATA", "dividend", 0, 2000.0, 0.0),
            CorpAction(date(2025, 6, 1), "TATA", "bonus", 50, 0, 0)]     # no cash — excluded
    fy = A.dividend_income_by_fy(acts)
    assert fy["FY2025-26"] == {"gross": 4200.0, "tds": 200.0, "net": 4000.0, "n": 2}
    assert fy["FY2026-27"]["net"] == 2000.0


def test_book_flows_untouched_by_actions_without_flows():
    fills = [BUY]
    assert book_flows(apply_actions(fills, [])) == book_flows(fills)


# ---- CLI surface -------------------------------------------------------------------

def test_cli_action_tax_and_actions(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    log = PlanLog()
    log.add_fill(date(2025, 5, 1), "TATA", "BUY", 100, 100.0, 0.0)
    log.add_fill(date(2025, 9, 1), "TATA", "SELL", 50, 120.0, 0.0)
    log.close()
    from indtrader.cli import main

    assert main(["action", "TATA", "dividend", "--cash", "4000", "--tds", "200",
                 "--date", "2025-08-01"]) == 0
    assert main(["action", "TATA", "merger"]) == 1                     # rejected, rc 1
    assert main(["actions"]) == 0
    out = capsys.readouterr().out
    assert "dividend TATA" in out and "4,000" in out
    assert main(["tax"]) == 0
    out = capsys.readouterr().out
    assert "dividend income: gross 4,200 - TDS 200 = net 4,000" in out
    assert main(["tax", "--setoff"]) == 0
    assert "dividend income gross 4,200" in capsys.readouterr().out
    assert main(["xirr"]) in (0, 1)                                    # no marks -> honest refusal ok
    get_settings.cache_clear()
