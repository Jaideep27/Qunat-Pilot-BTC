import pandas as pd
import pytest
from quantpilot.simulation.worker import reconciliation_events


def market_fixture():
    times=pd.date_range("2024-01-01",periods=6,freq="5min",tz="UTC")
    bars=pd.DataFrame(dict(event_time=times,available_at=times+pd.Timedelta(minutes=5),
                           open=100.,high=101.,low=99.,close=100.))
    marks=bars[["available_at"]].assign(mark_price=100.)
    funding=pd.DataFrame(dict(event_time=pd.to_datetime(["2024-01-01T00:00:00Z"]),funding_rate=.001))
    return dict(candles=bars,mark=marks,funding=funding)


def test_reconciliation_preserves_event_order_and_forbids_historical_entries():
    frames=market_fixture()
    _,events=reconciliation_events(frames,"2024-01-01T00:02:00+00:00")
    assert events
    assert all(e["replayed"] for e in events)
    assert all(pd.Timestamp(e["time"]) > pd.Timestamp("2024-01-01T00:02:00Z") for e in events)
    assert [pd.Timestamp(e["time"]) for e in events] == sorted(pd.Timestamp(e["time"]) for e in events)
    _,bootstrap=reconciliation_events(frames)
    assert len(bootstrap) == 1
    assert bootstrap[0]["kind"] == "bar"


def test_missing_candle_or_mark_stops_reconciliation_before_accounting():
    frames=market_fixture()
    frames["candles"]=frames["candles"].drop(index=2)
    with pytest.raises(ValueError,match="missing reconciliation candles"):
        reconciliation_events(frames,"2024-01-01T00:02:00Z")
    frames=market_fixture()
    frames["candles"]=frames["candles"].iloc[1:]
    with pytest.raises(ValueError,match="retained market history"):
        reconciliation_events(frames,"2024-01-01T00:02:00Z")
    frames=market_fixture()
    frames["mark"]=frames["mark"].iloc[1:]
    with pytest.raises(ValueError,match="missing marks"):
        reconciliation_events(frames,"2024-01-01T00:02:00Z")


def test_settlement_history_must_cover_reconciled_exposure():
    frames=market_fixture()
    frames["funding"]["event_time"]=pd.to_datetime(["2023-12-31T00:00:00Z"])
    with pytest.raises(ValueError,match="stale funding"):
        reconciliation_events(frames,"2024-01-01T00:02:00Z")



def test_reconciliation_allows_millisecond_jitter_but_rejects_missing_funding():
    times=pd.date_range("2024-01-01",periods=205,freq="5min",tz="UTC")
    bars=pd.DataFrame(dict(event_time=times,available_at=times+pd.Timedelta(minutes=5),
                           open=100.,high=101.,low=99.,close=100.))
    funding=pd.DataFrame(dict(event_time=pd.to_datetime(["2024-01-01T00:00:00Z",
        "2024-01-01T08:00:00.008Z","2024-01-01T16:00:00Z"],format="mixed"),funding_rate=.0001))
    frames=dict(candles=bars,mark=bars[["available_at"]].assign(mark_price=100.),funding=funding)
    _,events=reconciliation_events(frames,"2024-01-01T00:02:00Z")
    assert any(e["kind"] == "funding" and pd.Timestamp(e["time"]) == funding.event_time.iloc[1] for e in events)
    frames["funding"]=funding.drop(index=1)
    with pytest.raises(ValueError,match="funding settlement gap"):
        reconciliation_events(frames,"2024-01-01T00:02:00Z")

def test_settlement_after_last_bar_is_reconciled_before_checkpoint():
    frames=market_fixture()
    bar_end=frames['candles'].available_at.max()
    settlement=bar_end+pd.Timedelta(milliseconds=8)
    frames['funding']=pd.concat([frames['funding'],pd.DataFrame(dict(event_time=[settlement],funding_rate=[.001]))],ignore_index=True)
    _,events=reconciliation_events(frames,bar_end-pd.Timedelta(minutes=5),bar_end+pd.Timedelta(minutes=2))
    tail=[event for event in events if event['kind']=='funding' and pd.Timestamp(event['time'])==settlement]
    assert len(tail)==1 and tail[0]['mark']==100.
    assert pd.Timestamp(events[-1]['time'])==settlement
    _,default=reconciliation_events(frames,bar_end-pd.Timedelta(minutes=5))
    assert all(pd.Timestamp(event['time'])<=bar_end for event in default)
    with pytest.raises(ValueError,match='stale reconciliation cutoff'):
        reconciliation_events(frames,bar_end,bar_end+pd.Timedelta(minutes=11))
