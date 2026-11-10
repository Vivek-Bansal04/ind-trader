from contextlib import contextmanager
from datetime import date

import numpy as np
import pandas as pd

from indtrader.data.store import BarStore
from indtrader.execution.planlog import PlanLog

REASON = "breakout above 20d high with delivery volume"


@contextmanager
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_plan_lifecycle_plan_then_fill(tmp_path, monkeypatch, capsys):
    from indtrader.cli import main

    with env(tmp_path, monkeypatch):
        assert main(["plan", "TATA", "BUY", "--qty", "10", "--entry", "200", "--stop", "180",
                     "--target", "240", "--reason", REASON, "--setup", "breakout"]) == 0
        assert main(["fill", "TATA", "BUY", "10", "202", "--plan", "1"]) == 0
        log = PlanLog()
        row = log.con.execute("SELECT status, symbol FROM plans WHERE id=1").fetchone()
        note = log.con.execute("SELECT note FROM ledger WHERE id=1").fetchone()[0]
        log.close()
        assert row == ("executed", "TATA")           # fill closed the plan
        assert "plan#1" in note                       # fill carries the traceability
        out = capsys.readouterr().out
        assert "plan #1 -> executed" in out


def test_plan_refuses_thin_reason_and_bad_ordering(tmp_path, monkeypatch, capsys):
    from indtrader.cli import main

    with env(tmp_path, monkeypatch):
        assert main(["plan", "TATA", "BUY", "--qty", "10", "--entry", "200", "--stop", "180",
                     "--target", "240", "--reason", "vibes"]) == 1
        assert "rationale is mandatory" in capsys.readouterr().err
        assert main(["plan", "TATA", "BUY", "--qty", "10", "--entry", "200", "--stop", "210",
                     "--target", "240", "--reason", REASON]) == 1   # stop above entry
        assert "stop < entry < target" in capsys.readouterr().err
        log = PlanLog()
        assert log.con.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0
        log.close()


def test_fill_refuses_unknown_or_closed_plan(tmp_path, monkeypatch, capsys):
    from indtrader.cli import main

    with env(tmp_path, monkeypatch):
        assert main(["fill", "GHOST", "BUY", "1", "100", "--plan", "42"]) == 1
        assert "no plan #42" in capsys.readouterr().err
        assert main(["plan", "TATA", "BUY", "--qty", "10", "--entry", "200", "--stop", "180",
                     "--target", "240", "--reason", REASON]) == 0
        assert main(["plan-mark", "1", "cancelled"]) == 0
        assert main(["fill", "TATA", "BUY", "10", "202", "--plan", "1"]) == 1
        assert "not open" in capsys.readouterr().err
        log = PlanLog()
        assert len(log.fills()) == 0                  # refused fills never touch the ledger
        log.close()


def test_plans_all_toggle(tmp_path, monkeypatch, capsys):
    from indtrader.cli import main

    with env(tmp_path, monkeypatch):
        assert main(["plan", "TATA", "BUY", "--qty", "10", "--entry", "200", "--stop", "180",
                     "--target", "240", "--reason", REASON]) == 0
        capsys.readouterr()
        assert main(["plans"]) == 0
        assert "TATA" in capsys.readouterr().out
        assert main(["plan-mark", "1", "cancelled"]) == 0
        assert main(["plans"]) == 0
        assert "no open plans" in capsys.readouterr().out      # default view: open only
        assert main(["plans", "--all"]) == 0
        assert "cancelled" in capsys.readouterr().out


def seed_bars(tmp_path, uptrend=True, nifty=True):
    store = BarStore(tmp_path / "bars.sqlite")
    days = pd.bdate_range("2024-01-01", periods=300)
    rng = np.random.default_rng(11)
    for sym, base in (("STOCK", 100.0), ("NIFTY", 25000.0)):
        if sym == "NIFTY" and not nifty:
            continue
        drift = 40.0 if uptrend else -30.0
        c = base + np.linspace(0, drift, len(days)) + rng.normal(0, 1.0, len(days))
        c = np.maximum(c, base * 0.5)
        df = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99,
                           "close": c, "volume": 1e6}, index=pd.DatetimeIndex(days))
        store.upsert_candles(sym, "daily", df, "test")
    store.close()


def test_attribution_tags_setups_and_unplanned(tmp_path, monkeypatch, capsys):
    from indtrader.cli import main

    with env(tmp_path, monkeypatch):
        seed_bars(tmp_path)
        log = PlanLog(tmp_path / "plans.sqlite")
        log.add_fill(date(2025, 1, 6), "STOCK", "BUY", 10, 100.0, 0.0)
        log.add_fill(date(2025, 6, 2), "STOCK", "SELL", 10, 130.0, 0.0)
        log.add_fill(date(2025, 2, 3), "GHOST", "BUY", 5, 100.0, 0.0)
        log.add_fill(date(2025, 5, 2), "GHOST", "SELL", 5, 90.0, 0.0)
        # backdated executed plan: created BEFORE the 2025-01-06 buy (add_plan stamps now(),
        # and a plan dated after the fill is NOT the plan behind it — attribution proves that too)
        log.con.execute(
            "INSERT INTO plans(created,symbol,action,qty,entry,stop,target,reason,setup,status) "
            "VALUES ('2025-01-05 09:00:00','STOCK','BUY',10,100,90,130,?,'breakout','executed')",
            [REASON])
        log.con.commit()
        log.close()
        assert main(["attribution"]) == 0
        out = capsys.readouterr().out
        assert "breakout" in out and "unplanned" in out         # planned + orphan rows
        assert "up" in out                                      # index trend at entry
        assert "unplanned = fill without an executed plan" in out


def test_attribution_without_index_marks_trend_unknown(tmp_path, monkeypatch, capsys):
    from indtrader.cli import main

    with env(tmp_path, monkeypatch):
        seed_bars(tmp_path, nifty=False)
        log = PlanLog(tmp_path / "plans.sqlite")
        log.add_fill(date(2025, 1, 6), "STOCK", "BUY", 10, 100.0, 0.0)
        log.add_fill(date(2025, 6, 2), "STOCK", "SELL", 10, 130.0, 0.0)
        log.close()
        assert main(["attribution"]) == 0
        out = capsys.readouterr().out
        assert "?" in out                                       # no NIFTY bars -> unknown, not guessed


def test_compare_runs_all_strategies_side_by_side(tmp_path, monkeypatch, capsys):
    from indtrader.backtest.strategies import REGISTRY
    from indtrader.cli import main

    with env(tmp_path, monkeypatch):
        seed_bars(tmp_path, uptrend=True)
        assert main(["compare", "STOCK"]) == 0
        out = capsys.readouterr().out
        for name in REGISTRY:
            assert name in out                                  # every strategy in one table
        assert "buy&hold total" in out
        assert "buy&hold total +" in out                        # uptrend seed -> positive B&H note


def test_compare_rejects_unknown_strategy(tmp_path, monkeypatch, capsys):
    from indtrader.cli import main

    with env(tmp_path, monkeypatch):
        seed_bars(tmp_path)
        assert main(["compare", "STOCK", "--strategies", "magic"]) == 1
        assert "unknown strategy" in capsys.readouterr().err
