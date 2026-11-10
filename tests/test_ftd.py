"""FTD follow-through: deterministic fixtures + honest degradation (short data -> None).
Concept from the claude-skills market-regime skill (MIT); threshold is sigma-scaled."""
import numpy as np
import pandas as pd

from indtrader.research.regime import follow_through, flip_note, regime_from_data

SPK = {162: 80.0, 165: 80.0, 166: 80.0}  # the volume surges (base is a flat 50)
N = 176  # short tail so the FTD is still inside the watch window at the end


def series_from_gains(gains, volume=True):
    factors = [1 + gains.get(i, 0.0) for i in range(N)]
    close = pd.Series(100.0 * np.cumprod(factors),
                      index=pd.bdate_range("2024-01-01", periods=N))
    volume_s = pd.Series([SPK.get(i, 100.0 if i == 0 else 50.0) for i in range(N)],
                         index=close.index)
    return close, (volume_s if volume else None)


def fixture(kind="ftd"):
    base = {i: 0.02 for i in range(1, 101)}                     # uptrend, peak at i=100
    base.update({i: -0.0005 for i in range(101, 153)})          # slow grind down
    base.update({i: -0.01 for i in (153, 154, 155)})            # trough, close -5.5% from peak
    if kind == "undercut":
        base[166] = -0.03                                       # big down closes below trough
        return series_from_gains(base)
    base[160] = 0.001                                           # rally attempt day 1
    if kind == "failed":
        base[161] = -0.02                                       # day 2 undercuts the trough
        return series_from_gains(base)
    if kind == "early":
        base[162] = 0.02                                        # day 3: +2% + volume — too early
    base[165] = 0.02                                            # day 6: +2%, volume 80 > 50 -> FTD
    return series_from_gains(base)


def test_ftd_fires_and_min_day_is_enforced():
    out = follow_through(*fixture())
    assert out["state"] == "follow_through" and out["ftd_day_no"] == 6
    assert out["days_since"] == 176 - 1 - 165
    assert out["volume_checked"] is True
    early = follow_through(*fixture("early"))  # the day-3 spike must NOT count
    assert early["state"] == "follow_through" and early["ftd_day_no"] == 6


def test_undercut_resets_the_rally_attempt():
    assert follow_through(*fixture("undercut"))["state"] == "correction"


def test_big_day_without_volume_confirmation_is_no_ftd():
    c, _ = fixture()
    flat_vol = pd.Series(50.0, index=c.index)  # never higher than yesterday
    out = follow_through(c, flat_vol)
    assert out["state"] == "rally_attempt" and out["attempt_day_no"] == 176 - 1 - 160 + 1


def test_short_history_is_none_not_a_default_state():
    c, v = fixture()
    assert follow_through(c.iloc[:59], v.iloc[:59]) is None
    assert follow_through(c.iloc[:59], v.iloc[:59], lookback=30) is None  # never defaults
    out = follow_through(c, volume=None, lookback=100)  # volume optional, lookback honored
    assert out is not None and out["volume_checked"] is False


def test_flip_note_appends_ftd_line():
    from indtrader.research.regime import Regime
    r = Regime("OFF", 0.3, 0.2, False)
    c, v = fixture()
    txt = flip_note(r, follow_through(c, v))
    assert "sessions ago" in txt and "on volume" in txt
    c2, v2 = fixture("undercut")
    assert "correction" in flip_note(r, follow_through(c2, v2))


def test_regime_from_data_attaches_ftd_and_exposure_untouched():
    idx_c, idx_v = fixture()
    closes = {"A": pd.Series(np.linspace(100, 160, len(idx_c)), index=idx_c.index),
              "B": pd.Series(np.linspace(100, 150, len(idx_c)), index=idx_c.index)}
    r = regime_from_data(closes, idx_c, idx_v)
    assert r.ftd is not None and r.ftd["state"] == "follow_through"
    # 176 bars < 200-DMA => breadth/index unknown => honest NEUTRAL at 0.6, and the
    # scale changed because of THAT, never because of the FTD line.
    assert r.label == "NEUTRAL" and r.breadth is None and r.exposure_scale == 0.6
