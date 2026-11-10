import numpy as np
import pandas as pd

from indtrader.data.store import BarStore

SESSIONS = pd.bdate_range("2024-01-01", periods=320)


def fill(store, sym, dates):
    c = np.linspace(100, 140, len(dates))
    df = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e5},
                      index=pd.DatetimeIndex(dates))
    store.upsert_candles(sym, "daily", df, "test")


def test_weekly_risk_report_sections(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    store = BarStore(tmp_path / "bars.sqlite")
    fill(store, "NIFTY", SESSIONS)
    fill(store, "TATA", SESSIONS)
    univ = tmp_path / "universe.txt"
    univ.write_text("TATA\n", encoding="utf-8")
    store.close()

    from indtrader.execution.planlog import PlanLog
    from datetime import date, timedelta

    log = PlanLog(tmp_path / "plans.sqlite")
    log.add_fill(date(2024, 6, 3), "TATA", "BUY", 100, 110.0, 60.0)
    log.close()

    from indtrader.cli import main

    rc = main(["riskreport", "--no-telegram"])
    out = capsys.readouterr().out
    get_settings.cache_clear()
    assert rc == 0
    assert "WEEKLY RISK REPORT" in out
    assert "Regime" in out and "exposure" in out
    assert "Book:" in out and "XIRR" in out
    assert "Journal:" in out and "1 fills" in out
    assert "Data health" in out
