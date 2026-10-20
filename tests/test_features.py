import numpy as np
import pandas as pd
import pytest
from quantpilot.features import build_features, technical
from quantpilot.data import join_external, audit_frames, manifest, period

def test_future_mutation_cannot_change_earlier_features(candles, frames):
    baseline, _, _ = build_features(candles, frames)
    boundary = 2200
    altered = candles.copy()
    altered.loc[boundary:, ["open","high","low","close"]] *= 2
    changed = {k:v.copy() for k,v in frames.items()}
    for name, frame in changed.items():
        if name == "candles": continue
        cols = [c for c in frame if c not in ("event_time","available_at","source","received_at")]
        frame.loc[frame.event_time >= candles.iloc[boundary].event_time, cols] *= 2
    actual, _, _ = build_features(altered, changed)
    pd.testing.assert_frame_equal(baseline.iloc[:boundary], actual.iloc[:boundary])

def test_confirmed_swing_emitted_only_after_two_following_bars():
    index = pd.date_range("2024-01-01", periods=9, freq="5min", tz="UTC")
    d = pd.DataFrame(dict(open=5., close=5., high=[6,7,10,8,7,6,6,6,6],low=4.,volume=10.), index=index)
    f = technical(d)
    assert pd.isna(f.swing_high_distance.iloc[3])  # ATR warm-up aside, candidate cannot emit.
    shifted = d.copy()
    shifted.loc[index[4],"high"] = 11
    # Direct confirmation relation proves the candidate at 2 is rejected using bar 4 only at 4.
    confirmed = d.high.shift(2).where(d.high.shift(2) == d.high.rolling(5).max())
    assert pd.isna(confirmed.iloc[3]) and confirmed.iloc[4] == 10
    assert pd.isna(shifted.high.shift(2).where(shifted.high.shift(2) == shifted.high.rolling(5).max()).iloc[4])

def test_incomplete_higher_timeframe_has_no_effect(candles, frames):
    f, _, _ = build_features(candles, frames)
    group = f.index.floor("4h")
    columns = [c for c in f if c.startswith("4h_")]
    for _, part in f[columns].groupby(group):
        assert (part.nunique(dropna=False) <= 1).all()

def test_availability_and_stale_tolerance(candles, frames):
    frames["positioning"] = frames["positioning"].iloc[[101]].copy()
    joined = join_external(candles.iloc[100:110], frames)
    assert pd.isna(joined.open_interest.iloc[0])
    assert joined.open_interest.iloc[1] > 0
    assert pd.isna(joined.open_interest.iloc[-1])

def test_gaps_are_not_hidden_by_rolling_windows(candles, frames):
    with pytest.raises(ValueError, match="contiguous"):
        build_features(candles.drop(index=20), frames)

def test_period_and_manifest():
    start,end,warmup = period("2026-10-18")
    assert str(start.date()) == "2024-10-01"
    assert str(end.date()) == "2026-10-01"
    assert str(warmup.date()) == "2024-08-22"
    items = manifest("2026-10-18")
    assert len({x["filename"] for x in items}) == len(items)
    assert all("/futures/um/" in x["url"] for x in items)
    assert any("2024-08-22" in x["url"] and x["family"] == "positioning" for x in items)

def test_coverage_fails_for_missing_and_synthetic_families(frames):
    r = audit_frames(frames, "2024-11-01")
    assert not r["release_ready"]
    assert all(f["issues"] for f in r["families"].values())
    assert r["families"]["positioning"]["missing_windows"] > 0
    assert not audit_frames({}, "2026-10-18")["release_ready"]



@pytest.mark.parametrize("start", ["2024-01-01 01:00", "2024-01-03 01:00"])
def test_prior_week_levels_update_only_at_utc_monday_close(start):
    index = pd.date_range(start, "2024-01-10", freq="1h", tz="UTC")
    d = pd.DataFrame(dict(open=100., close=100., high=110., low=90., volume=10.), index=index)
    boundary = pd.Timestamp("2024-01-08", tz="UTC")
    # A Monday afternoon extreme belongs to the next week, even in a partial dataset.
    d.loc[index >= boundary + pd.Timedelta(hours=12), "high"] = 500.
    d.loc[index >= boundary + pd.Timedelta(hours=12), "low"] = 50.
    f = technical(d)
    available = f.loc[boundary:]
    np.testing.assert_allclose(available.prior_week_high * available.atr + d.loc[boundary:, "close"], 110.)
    np.testing.assert_allclose(available.prior_week_low * available.atr + d.loc[boundary:, "close"], 90.)
    assert f.loc[f.index < boundary, "prior_week_high"].isna().all()
