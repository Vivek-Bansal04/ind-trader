from datetime import date

import pytest

from indtrader.portfolio.harvest import harvest_candidates, lines
from indtrader.portfolio.tax import LedgerEntry, fy_of, open_lots, realize

BUY = LedgerEntry(date(2023, 5, 1), "TATA", "BUY", 100, 200.0, 10.0)


def test_open_lots_are_what_fifo_left_behind():
    entries = [BUY, LedgerEntry(date(2024, 5, 1), "TATA", "SELL", 40, 260.0, 5.0)]
    lots = open_lots(entries)
    assert len(lots) == 1 and lots[0].qty == 60 and lots[0].price == 200.0
    # consistency: realized + open cover every bought unit exactly once
    assert sum(m.qty for m in realize(entries)) + lots[0].qty == 100


def test_loss_and_free_reset_and_skips(tmp_path):
    entries = [
        LedgerEntry(date(2026, 1, 15), "LOSER", "BUY", 100, 300.0, 0.0),     # <12m, in loss
        LedgerEntry(date(2023, 1, 1), "WINNER", "BUY", 100, 100.0, 0.0),     # LTCG gain
        LedgerEntry(date(2023, 1, 1), "NOMARK", "BUY", 100, 100.0, 0.0),     # no mark
    ]
    marks = {"LOSER": 250.0, "WINNER": 150.0}  # NOMARK deliberately missing
    asof = date(2026, 11, 10)
    rep = harvest_candidates(entries, marks, [], asof)
    kinds = {c["symbol"]: (c["kind"], c["bucket"]) for c in rep["candidates"]}
    assert kinds["LOSER"] == ("loss-harvest", "STCG")
    assert kinds["WINNER"] == ("free-basis-reset", "LTCG")           # gain inside exemption
    assert rep["skipped_no_mark"] == ["NOMARK"]                       # named, never guessed
    loser = next(c for c in rep["candidates"] if c["symbol"] == "LOSER")
    assert loser["tax_effect"] < 0                                    # tax SAVED
    assert rep["financial_year"] == fy_of(asof)
    out = "\n".join(lines(rep))
    assert "exemption left 125,000" in out and "NOMARK" in out


def test_realized_ltcg_this_fy_eats_the_exemption():
    entries = [LedgerEntry(date(2023, 1, 1), "GAIN", "BUY", 100, 100.0, 0.0)]
    big_realized = [type("M", (), {"bucket": "LTCG", "net": 200_000.0,
                                   "sell_dt": date(2026, 5, 1)})()]
    rep = harvest_candidates(entries, {"GAIN": 150.0}, big_realized, date(2026, 11, 10))
    assert rep["exemption_left"] == 0.0
    c = rep["candidates"][0]
    assert c["kind"] == "taxable-basis-reset" and c["tax_effect"] > 0


def test_stcfresh_lot_gain_is_not_harvestable():
    entries = [LedgerEntry(date(2026, 1, 1), "NEW", "BUY", 100, 100.0, 0.0)]
    rep = harvest_candidates(entries, {"NEW": 150.0}, [], date(2026, 11, 10))
    assert rep["candidates"] == []          # <12m gain: nothing to reset yet
