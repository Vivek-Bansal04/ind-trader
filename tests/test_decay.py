import numpy as np

from indtrader.research.decay import decay_check


def base_trades(n=60, mu=200.0, sd=400.0, seed=7):
    rng = np.random.default_rng(seed)
    return list(rng.normal(mu, sd, n))


def test_refuses_on_thin_baseline():
    assert decay_check([100.0] * 19, []) is None      # < MIN_TRADES: unknown, not zero


def test_no_paper_yet_reports_such():
    out = decay_check(base_trades(), [])
    assert out["status"] == "no-paper-trades-yet"


def test_healthy_book_stays_within_band():
    out = decay_check(base_trades(), [150.0] * 15)    # paper ~ baseline mean
    assert out["status"] == "within band" and not out["below_p5_all"]


def test_decayed_book_gets_flagged_for_review():
    paper = [-900.0] * 15                             # far below the baseline's own p5 path
    out = decay_check(base_trades(), paper, capital=50_000.0)
    assert out["below_p5_all"] is True
    assert "REVIEW" in out["status"]
    assert any(w["below_p5"] for w in out["windows"])
