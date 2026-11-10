from datetime import date

import numpy as np
import pandas as pd

from indtrader.data.store import BarStore

SESSIONS = pd.bdate_range("2024-01-01", periods=320)


def fill(store, sym, dates):
    c = np.linspace(100, 140, len(dates))
    df = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e5},
                      index=pd.DatetimeIndex(dates))
    store.upsert_candles(sym, "daily", df, "test")


def run_review(tmp_path, monkeypatch, capsys, with_fills=True):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    store = BarStore(tmp_path / "bars.sqlite")
    fill(store, "NIFTY", SESSIONS)
    fill(store, "TATA", SESSIONS)
    univ = tmp_path / "universe.txt"
    univ.write_text("TATA\n", encoding="utf-8")
    if with_fills:
        from indtrader.execution.planlog import PlanLog

        log = PlanLog(tmp_path / "plans.sqlite")
        log.add_fill(date(2024, 6, 3), "TATA", "BUY", 100, 110.0, 60.0)
        from indtrader.models import OrderPlan

        pid = log.add_plan(OrderPlan(symbol="TATA", action="BUY", qty=100, entry=110.0,
                                     stop=100.0, target=130.0, reason="trend break with delivery",
                                     setup="breakout"))
        log.mark(pid, "executed")
        from indtrader.data.announce import store_announcements

        store_announcements(store, [{"symbol": "TATA", "d": date.today().isoformat(),
                                     "title": "Director resignation", "source": "test"}])
        log.close()
    store.close()
    from indtrader.cli import main

    rc = main(["review", "--no-telegram"])
    out = capsys.readouterr().out
    get_settings.cache_clear()
    return rc, out


def test_review_sections_and_journal_trace(tmp_path, monkeypatch, capsys):
    rc, out = run_review(tmp_path, monkeypatch, capsys)
    assert rc == 0
    assert "weekly review" in out
    assert "## Market" in out and "Regime" in out
    assert "## Book" in out and "XIRR" in out
    assert "thesis: trend break" in out            # the fill traces to its executed plan
    assert "## Journal" in out
    assert "all-clear" in out or "resignation" in out  # AMBER within 7d, or none if date drift
    assert "## Data health" in out


def test_review_flags_fill_without_plan(tmp_path, monkeypatch, capsys):
    run_review(tmp_path, monkeypatch, capsys, with_fills=False)
    # bars + universe are set; add an orphan fill directly
    from indtrader.config import get_settings
    from indtrader.execution.planlog import PlanLog

    log = PlanLog(tmp_path / "plans.sqlite")
    log.add_fill(date(2024, 6, 3), "TATA", "BUY", 100, 110.0, 60.0)
    log.close()
    from indtrader.cli import main

    rc = main(["review", "--no-telegram"])
    out = capsys.readouterr().out
    get_settings.cache_clear()
    assert rc == 0
    assert "NO executed plan behind this fill (journal gap)" in out


def test_scanner_results_proximity_note():
    from indtrader.research.scanner import evidence_row

    idx = pd.bdate_range("2023-01-01", periods=300)
    c = pd.Series(np.linspace(100, 200, len(idx)), index=idx)
    row = evidence_row("W", c, None, extra={"results_in": 1})
    assert "no fresh entry" in row["note"]
    row = evidence_row("W", c, None, extra={"results_in": 4})
    assert "half size" in row["note"]
    row = evidence_row("W", c, None, extra={"results_in": 30})
    assert "results" not in row["note"]
    row = evidence_row("W", c, None, extra=None)
    assert "results" not in row["note"]  # unknown event data -> no invented note
