from datetime import date

import pytest

from indtrader.data.ingest import import_into_log, parse_trade_csv
from indtrader.execution.planlog import PlanLog

CSV = """date,symbol,side,qty,price,costs
2024-05-02,TATA,buy,100,200.00,50
02/05/2024,tata,SELL,40,"2,600.00",25
"""


def write_csv(tmp_path, text=CSV, name="tb.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_generic_import_happy_path(tmp_path):
    entries = parse_trade_csv(write_csv(tmp_path))
    assert entries[0].symbol == "TATA" and entries[0].side == "BUY"
    assert entries[1].dt == date(2024, 5, 2) and entries[1].side == "SELL"
    assert entries[1].price == pytest.approx(2600.0)          # thousands separator stripped


def test_unknown_columns_refuse_naming_them(tmp_path):
    p = write_csv(tmp_path, "trade_date,ticker,buy_sell,quantity,avg_price\n2024-05-02,T,BUY,1,10\n")
    with pytest.raises(ValueError, match="absent from file"):
        parse_trade_csv(p)                                    # generic mapping doesn't fit: refuse


def test_column_map_rescues_the_same_file(tmp_path):
    p = write_csv(tmp_path, "trade_date,ticker,buy_sell,quantity,avg_price\n02-May-2024,T,bought,5,10.5\n")
    entries = parse_trade_csv(p, column_map={"dt": "trade_date", "symbol": "ticker",
                                             "side": "buy_sell", "qty": "quantity",
                                             "price": "avg_price"})
    assert entries[0].dt == date(2024, 5, 2) and entries[0].price == 10.5


def test_bad_row_aborts_with_row_number(tmp_path):
    p = write_csv(tmp_path, CSV + "2024-13-45,TATA,BUY,10,100,0\n")
    with pytest.raises(ValueError, match="row 4"):
        parse_trade_csv(p)


def test_import_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    entries = parse_trade_csv(write_csv(tmp_path))
    log = PlanLog()
    assert import_into_log(entries, log) == 2
    assert import_into_log(entries, log) == 0                 # re-run adds nothing
    log.close()
    get_settings.cache_clear()


def test_cli_import_writes_only_with_apply(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    from indtrader.cli import main

    p = write_csv(tmp_path)
    assert main(["import-csv", str(p)]) == 0                  # preview, no write
    log = PlanLog()
    assert len(log.fills()) == 0
    log.close()
    assert main(["import-csv", str(p), "--apply"]) == 0
    log = PlanLog()
    assert len(log.fills()) == 2
    log.close()
    get_settings.cache_clear()
