import hashlib
import json
import numpy as np
import pandas as pd
import pytest
import httpx
from quantpilot import repair as recovery
from quantpilot.data import VALUES


def row(t="2026-06-29T00:00:00Z", value=100., family="mark"):
    t=pd.Timestamp(t)
    d=dict(event_time=[t],available_at=[t+pd.Timedelta(minutes=5)],
           received_at=[t+pd.Timedelta(days=1)],source=["https://data.binance.vision/original"])
    d.update({column:[value] for column in VALUES[family]})
    return pd.DataFrame(d)


def test_only_exact_missing_timestamps_are_inserted():
    old=row()
    candidate=pd.concat([row(value=999.),row("2026-06-29T00:05:00Z",101.),row("2026-06-29T00:10:00Z",102.)])
    needed=pd.DatetimeIndex([pd.Timestamp("2026-06-29T00:05:00Z")])
    updated,count,conflicts=recovery.merge_verified(old,candidate,"mark",needed)
    assert count==1 and not conflicts
    assert list(updated.mark_price)==[100.,101.]
    assert updated.event_time.iloc[1]==needed[0]


def test_invalid_positioning_needs_agreeing_observed_fields():
    old=row(family="positioning",value=1.)
    old.loc[0,"top_account_ratio"]=np.nan
    candidate=row(family="positioning",value=1.)
    candidate.loc[0,"top_account_ratio"]=2.
    candidate["source"]="https://data.binance.vision/corrected"
    updated,count,conflicts=recovery.merge_verified(old,candidate,"positioning",pd.DatetimeIndex(old.event_time))
    assert count==1 and not conflicts
    assert updated.top_account_ratio.iloc[0]==2.
    assert updated.source.iloc[0].endswith("corrected")
    candidate.loc[0,"global_ratio"]=9.
    unchanged,count,conflicts=recovery.merge_verified(old,candidate,"positioning",pd.DatetimeIndex(old.event_time))
    assert count==0 and len(conflicts)==1
    assert pd.isna(unchanged.top_account_ratio.iloc[0])


def test_invalid_future_or_duplicate_repair_rows_are_rejected():
    old=row();wanted=pd.DatetimeIndex(old.event_time)
    bad=row(value=np.nan)
    assert recovery.merge_verified(old,bad,"mark",wanted)[1]==0
    future=row();future["available_at"]+=pd.Timedelta(days=1)
    with pytest.raises(ValueError,match="availability"):
        recovery.merge_verified(old,future,"mark",wanted)
    with pytest.raises(ValueError,match="duplicate"):
        recovery.merge_verified(old,pd.concat([old,old]),"mark",wanted)


def test_rest_repairs_keep_response_evidence_and_use_index_pair(tmp_path):
    (tmp_path/"raw").mkdir()
    day=pd.Timestamp("2026-06-29",tz="UTC")
    opened=int(day.timestamp()*1000)
    payload=[[opened,"100","102","99","101","0",opened+299999,"0",0,"0","0","0"]]
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=payload)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        frame,record=recovery.rest_candidate(client,tmp_path,"index",day)
    assert requests[0].url.params["pair"]=="BTCUSDT"
    assert "symbol" not in requests[0].url.params
    assert frame.index_price.iloc[0]==101.
    assert frame.event_time.iloc[0]==day
    assert hashlib.sha256((tmp_path/"raw"/record["filename"]).read_bytes()).hexdigest()==record["sha256"]
    payload[0][0]=opened+86400000
    payload[0][6]=payload[0][0]+299999
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError,match="outside requested"):
            recovery.rest_candidate(client,tmp_path,"index",day)


def test_offline_audit_rejects_missing_repair_evidence(tmp_path,monkeypatch):
    from quantpilot import data
    (tmp_path/"coverage.json").write_text(json.dumps(dict(dataset_hashes={},archives=[],
        repair_sources=[dict(filename="missing.json",sha256="0"*64)])))
    monkeypatch.setattr(data,"manifest",lambda as_of=None:[])
    with pytest.raises(ValueError,match="repair evidence checksum"):
        data.audit_cached(tmp_path)

@pytest.mark.parametrize("available",[True,False])
def test_repair_publication_is_hash_checked_and_fail_closed(tmp_path,monkeypatch,available):
    from quantpilot import data
    start=pd.Timestamp("2026-06-29",tz="UTC");end=start+pd.Timedelta(minutes=10)
    monkeypatch.setattr(data,"period",lambda as_of=None:(start,end,start))
    monkeypatch.setattr(recovery,"period",lambda as_of=None:(start,end,start))
    monkeypatch.setattr(data,"manifest",lambda as_of=None:[])
    times=pd.date_range(start,end,freq="5min",inclusive="left")
    frames={}
    for family,columns in VALUES.items():
        events=times
        if family=="funding": events=pd.DatetimeIndex([start])
        if family=="sentiment": events=pd.DatetimeIndex([start-pd.Timedelta(days=1)])
        delay=pd.Timedelta(minutes=5)
        if family=="funding": delay=pd.Timedelta(seconds=1)
        if family=="sentiment": delay=pd.Timedelta(days=1)
        frame=pd.DataFrame(dict(event_time=events,available_at=events+delay,
            received_at=end,source="https://data.binance.vision/original"))
        for column in columns: frame[column]=100.
        if family=="candles": frame["high"],frame["low"]=101.,99.
        frames[family]=frame
    missing=frames["index"].iloc[[1]].copy()
    frames["index"]=frames["index"].iloc[[0]]
    for family,frame in frames.items(): frame.to_parquet(tmp_path/(family+".parquet"),index=False)
    before=data.dataset_hashes(tmp_path)
    (tmp_path/"raw").mkdir()
    (tmp_path/"coverage.json").write_text(json.dumps(dict(archives=[],dataset_hashes=before)))
    item=dict(family="index",filename="daily.zip",url="https://data.binance.vision/daily.zip")
    monkeypatch.setattr(recovery,"archive_candidates",lambda *args:[item])
    def mock_fetch(item,root,client):
        if not available:return dict(item,status="missing",error="source unavailable")
        content=b"test corrected archive"
        (root/"raw"/item["filename"]).write_bytes(content)
        return dict(item,status="verified",sha256=hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(recovery,"fetch",mock_fetch)
    def mock_parse(record,root):
        result=missing.copy();result["source"]=record["url"];return result
    monkeypatch.setattr(recovery,"parse",mock_parse)
    def unavailable_rest(*args):raise RuntimeError("source unavailable")
    monkeypatch.setattr(recovery,"rest_candidate",unavailable_rest)
    result=recovery.repair(tmp_path)
    assert result["release_ready"]==available
    report=json.loads((tmp_path/"coverage.json").read_text())
    assert report["dataset_hashes"]==data.dataset_hashes(tmp_path)
    if available:
        assert result["changed_families"]==["index"]
        assert report["repair_sources"]
        assert data.audit_cached(tmp_path)["release_ready"]
    else:
        assert not result["changed_families"]
        assert data.dataset_hashes(tmp_path)==before
        assert not data.audit_cached(tmp_path)["release_ready"]
