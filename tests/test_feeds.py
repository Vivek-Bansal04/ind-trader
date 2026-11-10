import numpy as np
import pandas as pd

from indtrader.data import feeds
from indtrader.data.store import BarStore
from indtrader.research.scanner import scan

DELIV_CSV = """SYMBOL,SERIES,DATE1,OPEN_PRICE,CLOSE_PRICE,TTL_TRD_QNTY,NO_OF_TRADES,DELIV_QTY,DELIV_PER
RELIANCE,EQ,31-10-2026,1400,1410,1000000,50000,600000,60.0
TCS,EQ,31-10-2026,300,310,500000,20000,50000,10.0
INFY,EQ,31-10-2026,1500,1510,400000,15000,,
TITAN,EQ,31-10-2026,3400,3450,100000,5000,20000,
BEML1,EQ,30-10-2026,100,101,1000,10,500,50.0
XYZA,EQ,BADDATE,1,2,10,1,5,50.0
"""

DEAL_ROWS = [
    {"BD_DT_DATE": "30-10-2026", "BD_DT_ORDER": "1", "BD_SYMBOL": "reliance",
     "BD_CLIENT_NAME": "ABC Funds", "BD_BUY_SELL": "B", "BD_QTY_TRD": 100000, "BD_TP_WATP": 1410.0},
    {"BD_DT_DATE": "30-10-2026", "BD_DT_ORDER": "2", "BD_SYMBOL": "TCS",
     "BD_CLIENT_NAME": "XYZ Ltd", "BD_BUY_SELL": "S", "BD_QTY_TRD": 20000, "BD_TP_WATP": 305.0},
    {"BD_SYMBOL": "GARBAGE"},  # must be dropped, not crash
]


def test_parse_delivery_csv():
    df = feeds.parse_delivery_csv(DELIV_CSV)
    by = df.set_index("symbol")
    assert by.loc["RELIANCE", "deliv_per"] == 60.0
    assert by.loc["TITAN", "deliv_per"] == 20.0          # computed 20000/100000*100
    assert "INFY" not in by.index                        # no deliv data at all -> dropped
    assert "XYZA" not in by.index                        # bad date dropped
    assert by.loc["RELIANCE", "d"] == "2026-10-31"


def test_parse_bulk_deals_drops_garbage():
    df = feeds.parse_bulk_deals(DEAL_ROWS)
    assert len(df) == 2
    assert df.iloc[0]["symbol"] == "RELIANCE" and df.iloc[0]["side"] == "B"


def test_store_roundtrip_and_reads(tmp_path):
    store = BarStore(path=tmp_path / "bars.sqlite")
    try:
        feeds.upsert_delivery(store, feeds.parse_delivery_csv(DELIV_CSV))
        # second day so avg has history
        day2 = "SYMBOL,SERIES,DATE1,OPEN_PRICE,CLOSE_PRICE,TTL_TRD_QNTY,NO_OF_TRADES,DELIV_QTY,DELIV_PER\n" + \
               "RELIANCE,EQ,30-10-2026,1390,1400,1000000,50000,400000,40.0\n"
        feeds.upsert_delivery(store, feeds.parse_delivery_csv(day2))
        assert feeds.latest_delivery_per(store, "reliance") == 60.0
        assert feeds.avg_delivery_per(store, "RELIANCE") == 50.0
        assert feeds.latest_delivery_per(store, "NOPE") is None

        n = feeds.upsert_deals(store, feeds.parse_bulk_deals(DEAL_ROWS))
        assert n == 2
        # window wide enough that fixture dates stay inside regardless of run date
        assert feeds.net_bulk_value(store, "RELIANCE", days=365) == 100000 * 1410.0
        assert feeds.net_bulk_value(store, "TCS", days=365) == -20000 * 305.0
        # idempotent re-ingest
        assert feeds.upsert_deals(store, feeds.parse_bulk_deals(DEAL_ROWS)) == 2
        assert feeds.net_bulk_value(store, "RELIANCE", days=365) == 100000 * 1410.0
    finally:
        store.close()


def test_net_bulk_none_when_never_ingested(tmp_path):
    store = BarStore(path=tmp_path / "bars.sqlite")
    try:
        feeds.ensure_feed_tables(store)
        store.con.execute("DELETE FROM deals")
        store.con.commit()
        assert feeds.net_bulk_value(store, "RELIANCE") is None  # unknown != zero
    finally:
        store.close()


def _closes(seed, drift, n=300):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-01", periods=n)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n))), index=idx)


def _rising(end, n=300):  # deterministic uptrend so scores are exact
    return pd.Series(np.linspace(100.0, end, n), index=pd.bdate_range("2023-01-01", periods=n))


def test_scan_extras_boost_and_annotate():
    closes = {"A": _rising(170), "B": _rising(165)}
    bench = _rising(120)
    extras = {
        "A": {"deliv_per": 5.0, "deliv_avg": 60.0, "net_bulk": 1_000_000.0},
        "B": {"deliv_per": 70.0, "deliv_avg": 60.0, "net_bulk": None},
    }
    base = scan(closes, bench=bench).set_index("symbol")
    out = scan(closes, bench=bench, extras=extras).set_index("symbol")
    assert bool(out.loc["A", "tradable"]) and bool(out.loc["B", "tradable"])
    assert "low delivery on trend" in out.loc["A", "note"]
    assert "low delivery" not in str(out.loc["B", "note"])
    assert out.loc["A", "deliv_per"] == 5.0
    # exactly +0.5 for net bulk buying on A; B untouched
    assert out.loc["A", "score"] - base.loc["A", "score"] == 0.5
    assert out.loc["B", "score"] == base.loc["B", "score"]


def test_scan_without_extras_unchanged():
    out = scan({"A": _rising(170)}, bench=_rising(120))
    assert "deliv_per" not in out.columns
