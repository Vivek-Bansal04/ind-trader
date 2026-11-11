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


# ---- split-gap: adj_coverage=1.0 must not hide an uncovered split cliff (round-4) --

def seed_split(store, sym, factor_fn):
    """Flat 100 closes until 2024-02-15, then halved — a 1:2 split shape (-50%)."""
    split_day = pd.Timestamp("2024-02-15")
    closes = [100.0] * (len(SESSIONS))
    half = int(np.searchsorted(SESSIONS, split_day))
    closes[half:] = [50.0] * (len(closes) - half)
    df = pd.DataFrame({"open": closes, "high": closes, "low": closes,
                       "close": closes, "volume": 1e5}, index=SESSIONS)
    store.upsert_candles(sym, "daily", df, "test")
    store.upsert_adj_factors(sym, factor_fn(half), "test")


def run_split_health(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    store = BarStore(tmp_path / "bars.sqlite")
    fill(store, "NIFTY", SESSIONS)
    # factors that DO cover the split: 0.5 before the day, 1.0 from it -> step 2.0
    def covered(half):
        return pd.Series([0.5] * half + [1.0] * (len(SESSIONS) - half), index=SESSIONS)

    # dividend-only factors (the real HDFCBANK lie): step ~1.0 on split day
    def dividends_only(half):
        return pd.Series(0.985, index=SESSIONS)

    seed_split(store, "SPLITCOVERED", covered)
    seed_split(store, "SPLITGAP", dividends_only)
    fill(store, "FLAT", SESSIONS)  # no factors, no jumps -> must not be flagged
    univ = tmp_path / "universe.txt"
    univ.write_text("SPLITCOVERED\nSPLITGAP\nFLAT\n", encoding="utf-8")
    store.close()
    from indtrader.cli import main

    rc = main(["health", "--file", str(univ)])
    out = capsys.readouterr().out
    get_settings.cache_clear()
    return rc, out


def test_health_names_uncovered_split_cliffs(tmp_path, monkeypatch, capsys):
    rc, out = run_split_health(tmp_path, monkeypatch, capsys)
    assert "SPLIT-GAP: 1 symbol(s)" in out           # the lie, named
    assert "SPLITGAP" in out and "-50%" in out       # date+magnitude printed
    assert "factor step 1.0" in out                  # what the factors actually did
    assert "SPLITCOVERED: 2024-02-15" not in out     # covered split NOT flagged
    assert rc == 0                                    # print, not gate (by design)


def test_health_split_gap_survives_no_factors(tmp_path, monkeypatch, capsys):
    """Zero factors + big cliff: step is None -> gap, no crash, message readable."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    store = BarStore(tmp_path / "bars.sqlite")
    fill(store, "NIFTY", SESSIONS)
    seed_split(store, "SPLITGAP", lambda half: pd.Series(dtype="float64"))
    univ = tmp_path / "universe.txt"
    univ.write_text("SPLITGAP\n", encoding="utf-8")
    store.close()
    from indtrader.cli import main

    rc = main(["health", "--file", str(univ)])
    out = capsys.readouterr().out
    get_settings.cache_clear()
    assert rc == 0 and "SPLIT-GAP: 1 symbol(s)" in out and "SPLITGAP" in out
