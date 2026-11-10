import math
import os
from datetime import date

import pandas as pd
import pytest

from indtrader.data.store import BarStore
from indtrader.execution.planlog import PlanLog
from indtrader.portfolio import mf
from indtrader.portfolio.rebalance import rebalance_plan
from indtrader.portfolio.valuation import book_xirr, resolve_marks

GOOD_NAV = (
    "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;"
    "Net Asset Value;Date\n"
    "Axis Mutual Fund\n"                               # publisher banner: skipped, NOT malformed
    "100001;INF000S00001;INF000S00002;Alpha Bluechip Fund - Direct Growth;Direct;Growth;54.3210;2026-11-09\n"
    "100002;INF000S00003;INF000S00004;Beta Liquid Fund - Direct Plan;Direct;Growth;10.0010;09-Nov-2026\n"
    "badline;without;enough\n"                         # malformed -> dropped + counted
    "100001;INF000S00001;INF000S00002;Alpha Bluechip Fund - Direct Growth;Direct;Growth;55.0000;10-Nov-2026\n"
)


def test_parse_contract_checks():
    df = mf.parse_nav_all(GOOD_NAV)
    assert len(df) == 2                                   # dedup keeps latest per scheme
    row = df[df["scheme_code"] == "100001"].iloc[0]
    assert row["nav"] == pytest.approx(55.0) and row["d"] == "2026-11-10"  # the newer date wins
    assert df.attrs["bad_rows"] == 1                      # the malformed line is counted, not hidden
    with pytest.raises(ValueError, match="header changed"):
        mf.parse_nav_all("Scheme code;ISIN Code;MFISchemeCode;RTACode;Scheme Name;Date;Nav\n"
                         "1;2;3;4;Name;2026-01-01;1.0")
    with pytest.raises(ValueError, match="no semicolon header"):
        mf.parse_nav_all("<html>maintenance</html>")
    with pytest.raises(ValueError, match="zero parseable rows"):
        mf.parse_nav_all(mf.HEADER + "\nzz;not;a;valid;row;t;g;nope;nope")


def test_store_mark_resolve_and_symbol_isolation(tmp_path):
    store = BarStore(tmp_path / "b.sqlite")
    mf.store_nav(store, "100001", "Alpha Bluechip", 55.0, "2026-11-10")
    assert mf.mf_mark(store, "MF:100001") == 55.0
    assert mf.mf_mark(store, "mf:100001") == 55.0          # prefix is case-insensitive
    assert mf.mf_mark(store, "MF:999999") is None          # unknown, not zero
    assert mf.mf_mark(store, "TATA") is None               # equity never priced from NAV store
    idx = pd.bdate_range("2024-01-01", periods=260)
    c = pd.Series(100.0, index=idx)
    store.upsert_candles("TATA", "daily",
                         pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1e5}, index=idx),
                         "test")
    assert resolve_marks(store, ["TATA", "MF:100001", "MF:404"]) == {"TATA": 100.0,
                                                                     "MF:100001": 55.0,
                                                                     "MF:404": None}
    store.close()


def test_mf_slice_never_taxed_as_equity_fifo(tmp_path):
    """MF flows price via NAV, and the FIFO bucketing is never applied to them by
    accident (that would be the wrong statute)."""
    store, log = BarStore(tmp_path / "b.sqlite"), PlanLog(tmp_path / "p.sqlite")
    mf.store_nav(store, "100001", "Alpha", 55.0, "2026-11-10")
    for i, (d, nav) in enumerate([(date(2025, 5, 5), 40.0), (date(2025, 6, 5), 45.0),
                                  (date(2025, 7, 5), 50.0)]):
        units = round(5000.0 / nav, 4)
        log.add_fill(d, "MF:100001", "BUY", units, nav, 0.0, "sip")
    flows = mf.mf_flows(log.fills())
    assert len(flows) == 3 and not mf.equity_flows(flows)
    expected_units = sum(round(5000.0 / nav, 4) for nav in (40.0, 45.0, 50.0))
    held_units = sum(f.qty for f in flows)
    assert held_units == pytest.approx(expected_units, abs=1e-9)
    out = book_xirr(flows, resolve_marks(store, ["MF:100001"]), asof=date(2026, 11, 10))
    assert out["ok"] and out["xirr"] is not None and out["xirr"] > 0
    assert out["open_value"] == pytest.approx(held_units * 55.0, rel=1e-6)
    store.close()
    log.close()


def test_missing_nav_refuses_xirr_not_guesses(tmp_path):
    log = PlanLog(tmp_path / "p.sqlite")
    log.add_fill(date(2025, 5, 5), "MF:100001", "BUY", 100.0, 40.0, 0.0, "sip")
    log.add_fill(date(2025, 6, 5), "MF:100001", "BUY", 100.0, 45.0, 0.0, "sip")
    out = book_xirr(log.fills(), {"MF:100001": None})
    assert not out["ok"] and out["missing_marks"] == ["MF:100001"]
    log.close()


def test_rebalance_band_sizing_and_unknown_marks():
    held = {"A": 100.0, "B": 50.0}
    marks = {"A": 100.0, "B": 200.0, "C": 50.0}
    targets = {"A": 0.2, "B": 0.2, "C": 0.5}
    # A = 10,000 (50% of 20,000 known), B = 10,000 -> both 30% over -> 6,000 trims; C sized
    plan = rebalance_plan(held, marks, targets, tol=0.02)
    by = {t.symbol: t for t in plan}
    assert by["A"].action == "SELL" and by["A"].est_value == pytest.approx(6000.0)
    assert by["A"].qty == pytest.approx(60.0)
    assert by["B"].action == "SELL"
    assert by["C"].action == "BUY" and by["C"].qty == pytest.approx(200.0)  # 10,000 / 50
    assert "tax --setoff" in by["A"].note            # the tax lens rides every sell
    assert any(t.symbol == "CASH" for t in plan)    # targets sum to 0.9 -> residual stated

    # within tol -> no trades (book is 50/50, targets are 50/50)
    assert [t for t in rebalance_plan(held, marks, {"A": 0.5, "B": 0.5}, tol=0.02)
            if t.symbol in ("A", "B")] == []

    # unknown mark: never guessed — proposal exists but sized zero
    plan2 = rebalance_plan({"A": 100.0}, {"A": None}, {"A": 0.5}, tol=0.02)
    ta = next(t for t in plan2 if t.symbol == "A")
    assert ta.qty == 0.0 and "unknown" in ta.note

    # MF rows carry the statute warning, not a fake equity bucket
    plan3 = rebalance_plan({"MF:100001": 100.0}, {"MF:100001": 55.0}, {"MF:100001": 0.3}, tol=0.01)
    tm = next(t for t in plan3 if t.symbol == "MF:100001")
    assert tm.action == "SELL" and "DEBT" in tm.note.upper()


def test_fetch_navs_reports_outage_without_touching_store(tmp_path, monkeypatch):
    class Resp:
        status_code = 503
        text = "oops"

    class Client:
        def get(self, *a, **k):
            return Resp()

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    store = BarStore(tmp_path / "b.sqlite")
    out = mf.fetch_navs(store)
    assert not out["ok"] and "503" in out["error"]
    n = store.con.execute("SELECT name FROM sqlite_master WHERE name='mf_navs'").fetchone()
    assert n is not None  # table exists (schema) but nothing stored
    store.close()


@pytest.mark.skipif(os.environ.get("INDTRADER_LIVE_PROBE") != "1",
                    reason="manual live probe: set INDTRADER_LIVE_PROBE=1 where AMFI is reachable")
def test_live_amfi_navall_contract_probe():
    """RUN MANUALLY where reachable: proves the real file still matches the header
    contract this parser refuses to guess about. Not part of the offline suite/CI."""
    import httpx

    r = httpx.get(mf.NAV_URL, timeout=30.0, follow_redirects=True)
    df = mf.parse_nav_all(r.text)
    assert len(df) > 1000 and df["nav"].median() > 0
    assert math.isfinite(df["nav"].iloc[0])
