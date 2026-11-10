import numpy as np
import pandas as pd

from indtrader.data.store import BarStore


def seed(tmp_path, nifty_end_lag_bdays: int):
    """Store with fresh GOOD bars + NIFTY bars ending N business days before today."""
    store = BarStore(tmp_path / "bars.sqlite")
    today = pd.Timestamp.today().normalize()
    idx_days = pd.bdate_range(end=today - pd.offsets.BDay(nifty_end_lag_bdays), periods=320)
    good_days = pd.bdate_range(end=today, periods=320)
    rng = np.random.default_rng(3)
    for sym, days, base in (("NIFTY", idx_days, 25000.0), ("GOOD", good_days, 500.0)):
        c = base + np.cumsum(rng.normal(0, 2, len(days)))
        df = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99,
                           "close": c, "volume": 1e6}, index=pd.DatetimeIndex(days))
        store.upsert_candles(sym, "daily", df, "test")
    store.close()
    univ = tmp_path / "universe.txt"
    univ.write_text("GOOD\n", encoding="utf-8")
    return univ


def run_scan(tmp_path, monkeypatch, capsys, lag, *extra):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from indtrader.config import get_settings

    get_settings.cache_clear()
    univ = seed(tmp_path, lag)
    from indtrader.cli import main

    rc = main(["scan", "--file", str(univ), "--no-journal", *extra])
    cap = capsys.readouterr()
    get_settings.cache_clear()
    return rc, cap


def test_stale_index_refuses_the_scan(tmp_path, monkeypatch, capsys):
    rc, cap = run_scan(tmp_path, monkeypatch, capsys, 30)        # 30 business days old
    assert rc == 1
    assert "DEAD-MAN" in cap.err and "--ignore-stale" in cap.err
    assert "regime=" not in cap.out                              # refused BEFORE any signals


def test_fresh_index_scans_normally(tmp_path, monkeypatch, capsys):
    rc, cap = run_scan(tmp_path, monkeypatch, capsys, 0)
    assert rc == 0 and "regime=" in cap.out


def test_ignore_stale_is_a_loud_override(tmp_path, monkeypatch, capsys):
    rc, cap = run_scan(tmp_path, monkeypatch, capsys, 30, "--ignore-stale")
    assert "DEAD-MAN" not in cap.err                             # operator chose to proceed
    assert rc in (0, 1)                                          # scan itself decides, not the guard
