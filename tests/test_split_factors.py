"""Split-factor math tests (round-5 review).

The laptop's first combined_factors multiplied yfinance factors by a split
cumprod blindly. yfinance back-adjustment ALREADY multiplies pre-action days
by the action's multiplier when it records one — so blind multiplication
double-halves any split yf did record, while yf's real-world dividend-only
behaviour (the HDFCBANK proof) still needs the merge. These tests pin both
sides of that, plus the ONE-convention _split_ratio rule the review landed
(the first version mixed split=b/a with face-value=a/b into one cumprod).
"""
import numpy as np
import pandas as pd
import pytest

from indtrader.data.store import BarStore
from indtrader.research import coractions as ca


@pytest.fixture
def store(tmp_path):
    s = BarStore(tmp_path / "b.sqlite")
    yield s
    s.close()


DATES = pd.bdate_range("2025-08-20", "2025-08-29")     # 8 sessions, split mid
SPLIT = pd.Timestamp("2025-08-25")


def seed_bars(store, closes):
    c = np.asarray(closes, dtype=float)
    store.upsert_candles("SYM", "daily",
                         pd.DataFrame({"open": c, "high": c, "low": c, "close": c,
                                       "volume": [1e5] * len(c)}, index=DATES), "test")


def adj_of(store):
    return store.load("SYM", adjusted=True, validate=False)["close"]


# ---- _split_ratio: ONE convention ------------------------------------------------------

def test_split_ratio_single_convention():
    assert ca._split_ratio("Split of shares 1:2") == 0.5        # price halves
    assert ca._split_ratio("Stock split: from 1 share to 5 shares") is None  # no ratio -> None, NOT silent 1.0
    assert ca._split_ratio("BONUS ISSUE 1:1") == 0.5            # shares double
    assert ca._split_ratio("Bonus 1:2") == round(1 / 3, 6)      # 3 shares from 1
    assert ca._split_ratio("Face value split from Rs.10 to Rs.2") == 0.2
    assert ca._split_ratio("Dividend Rs 12 per share") is None  # dividend: never a factor
    assert ca._split_ratio("Board meeting intimation") is None


# ---- merge: yf dividend-only + real split (the HDFCBANK case) ---------------------------

def test_merge_fixes_the_hdfcbank_cliff(store, capsys):
    seed_bars(store, [100, 100, 100, 50, 50, 50, 50, 50])     # raw halves ON SPLIT
    div_only = pd.Series(1.0, index=DATES)                      # yf: flat 1.0 through the cliff
    store.upsert_adj_factors("SYM", div_only, "yfinance")
    store.upsert_split_factors("SYM", {SPLIT.date().isoformat(): 0.5})
    adj = adj_of(store)
    assert adj.iloc[-1] == 50.0                                 # today stays raw (yf convention)
    # pre-split day adjusted = 100 * 0.5 = 50 -> the -50% day is continuous now
    pre = adj.loc[DATES < SPLIT]
    assert (abs(pre - 50.0) < 1e-9).all()
    assert abs(adj.pct_change().min()) < 1e-9                   # cliff GONE
    assert "RAW" not in capsys.readouterr().out                 # factors existed, no fallback print


def test_merge_never_double_counts_a_split_yf_already_has(store):
    seed_bars(store, [100, 100, 100, 50, 50, 50, 50, 50])
    yf = pd.Series(1.0, index=DATES)
    yf[DATES < SPLIT] = 0.5                                     # yf DID record the split
    store.upsert_adj_factors("SYM", yf, "yfinance")
    store.upsert_split_factors("SYM", {SPLIT.date().isoformat(): 0.5})
    adj = adj_of(store)
    # combined must equal yf's own adjustment — NOT 0.25 (blind product would give 25.0)
    assert abs(adj.loc[DATES[0]] - 50.0) < 1e-9
    assert adj.loc[DATES < SPLIT].std() < 1e-12


def test_merge_anchor_is_newest_day_and_pre_feed_level(store):
    seed_bars(store, [90] * 8)
    # yf feed starts mid-history with a dividend step already below 1.0
    yf = pd.Series([0.9, 0.9, 0.95, 0.95, 1.0, 1.0, 1.0, 1.0], index=DATES)
    store.upsert_adj_factors("SYM", yf, "yfinance")
    store.upsert_split_factors("SYM", {DATES[1].date().isoformat(): 0.5})  # older split, pre-yf-feed
    adj = adj_of(store)
    assert adj.iloc[-1] == 90.0                                 # newest day raw
    # day 0 sits BEFORE the split day -> gets the 0.5 split weight on top of yf's level
    assert abs(adj.iloc[0] - 90 * 0.5 * 0.9) < 1e-9


def test_two_splits_stack_and_stop_at_the_latest(store):
    seed_bars(store, [100] * 8)
    store.upsert_split_factors("SYM", {DATES[2].date().isoformat(): 0.5,
                                       DATES[5].date().isoformat(): 0.5})
    adj = adj_of(store)                                         # no yf factors: split-only path
    assert adj.iloc[-1] == 100.0                                # after last split: raw
    assert adj.iloc[5] == 100.0                                 # ON ex-date: raw already post-split
    assert abs(adj.iloc[4] - 50.0) < 1e-9                       # between splits: one 0.5
    assert abs(adj.iloc[0] - 25.0) < 1e-9                       # before both: 0.25


# ---- unresolved splits are REPORTED, never silent no-ops ---------------------------------

def test_build_split_factors_reports_unresolved(store):
    events = [{"date": "2025-08-26", "kind": "Split of shares 1:2", "ratio": None},
              {"date": "2025-09-01", "kind": "Bonus", "ratio": None},          # no numbers!
              {"date": "2025-10-01", "kind": "Dividend Rs 5", "ratio": None}]  # not a split
    n, unresolved = ca.build_split_factors(store, "sym", events)
    assert n == 1                                               # only the 1:2 stored
    assert unresolved == ["2025-09-01: Bonus"]                  # the unparseable one is NAMED
    sf = store.split_factors("SYM")
    assert list(sf.values) == [0.5]
