import numpy as np
import pandas as pd
from quantpilot import data
from quantpilot.features import build_features


def real_sources(frames):
    for frame in frames.values(): frame["source"]="https://public.example/historical"
    return frames


def test_quarantine_retains_period_and_families_and_keeps_strict_failure(frames,monkeypatch):
    frames=real_sources(frames)
    start=frames["candles"].event_time.min()
    end=frames["candles"].available_at.max()
    monkeypatch.setattr(data,"period",lambda as_of=None:(start,end,start))
    frames["positioning"].loc[1000,"top_account_ratio"]=np.nan
    frames["positioning"]=frames["positioning"].drop(index=1100)
    strict=data.audit_frames(frames)
    accepted=data.audit_frames(frames,policy={"mode":"positioning_quarantine_v1"})
    assert not strict["release_ready"]
    assert accepted["release_ready"] and not accepted["strict_release_ready"]
    assert accepted["quarantine"]["source_rows"]==2
    assert accepted["start"]==strict["start"] and accepted["end_exclusive"]==strict["end_exclusive"]
    assert set(accepted["families"])==set(data.VALUES)
    # A structural error or another family's gap is never waived.
    frames["mark"]=frames["mark"].drop(index=1200)
    assert not data.audit_frames(frames,policy={"mode":"positioning_quarantine_v1"})["release_ready"]


def test_quarantine_rejects_systematic_loss_and_bad_provenance(frames,monkeypatch):
    frames=real_sources(frames)
    start=frames["candles"].event_time.min();end=frames["candles"].available_at.max()
    monkeypatch.setattr(data,"period",lambda as_of=None:(start,end,start))
    frames["positioning"].loc[1000:1004,"top_account_ratio"]=np.nan
    assert not data.audit_frames(frames,policy={"mode":"positioning_quarantine_v1"})["release_ready"]
    frames["positioning"].loc[1000:1004,"top_account_ratio"]=1.
    frames["positioning"].loc[1000,"source"]="fixture"
    assert not data.audit_frames(frames,policy={"mode":"positioning_quarantine_v1"})["release_ready"]


def test_missing_positioning_and_lookbacks_cannot_enter_any_experiment(frames):
    candles=frames["candles"]
    baseline,_,_=build_features(candles,frames)
    frames["positioning"]=frames["positioning"].drop(index=1000)
    actual,_,joined=build_features(candles,frames)
    times=candles.available_at
    assert pd.isna(joined.loc[times.iloc[1000],"open_interest"])
    assert actual.loc[times.iloc[1000:1013],"open_interest"].isna().all()
    assert actual.loc[times.iloc[1013],"open_interest"]>0
    pd.testing.assert_frame_equal(baseline.loc[:times.iloc[999]],actual.loc[:times.iloc[999]])
    assert actual.loc[times.iloc[1000:1013]].isna().any(axis=1).all()
