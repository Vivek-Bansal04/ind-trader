import numpy as np
import pandas as pd

from indtrader.data.store import BarStore

SESSIONS = pd.bdate_range("2024-01-01", periods=100).drop(
    pd.to_datetime(["2024-01-26", "2024-03-25", "2024-05-01"]))


def fill(store, sym, dates):
    c = np.linspace(100, 120, len(dates))
    df = pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e5},
                      index=pd.DatetimeIndex(dates))
    store.upsert_candles(sym, "daily", df, "test")


def run_health(tmp_path, monkeypatch, capsys, *extra):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    store = BarStore(tmp_path / "bars.sqlite")
    fill(store, "NIFTY", SESSIONS)
    fill(store, "GOOD", SESSIONS)
    fill(store, "GAPPY", SESSIONS.drop(pd.to_datetime(["2024-02-14", "2024-02-15"])))
    univ = tmp_path / "universe.txt"
    univ.write_text("GOOD\nGAPPY\nMISSING\n", encoding="utf-8")
    store.close()
    from indtrader.cli import main

    rc = main(["health", "--file", str(univ), *extra])
    get_settings.cache_clear()
    return rc, capsys.readouterr().out


def test_health_flags_gaps_and_absent_symbols(tmp_path, monkeypatch, capsys):
    rc, out = run_health(tmp_path, monkeypatch, capsys, "--max-missing", "0", "--max-stale", "0")
    assert rc == 1
    assert "GAPPY" in out and "MISSING" in out and "problem symbol(s) of 3" in out
    assert "GOOD" in out  # listed, but not counted as a problem line-wise either way


def test_health_passes_when_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    store = BarStore(tmp_path / "bars.sqlite")
    fill(store, "NIFTY", SESSIONS)
    fill(store, "GOOD", SESSIONS)
    univ = tmp_path / "universe.txt"
    univ.write_text("GOOD\n", encoding="utf-8")
    store.close()
    from indtrader.cli import main

    rc = main(["health", "--file", str(univ), "--max-missing", "0", "--max-stale", "0"])
    out = capsys.readouterr().out
    get_settings.cache_clear()
    assert rc == 0 and "0 problem symbol(s) of 1" in out
