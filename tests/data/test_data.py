import hashlib
import io
import zipfile
import httpx
import pandas as pd
import pytest
from quantpilot.data.archives import fetch, parse, audit_frames, VALUES

def zipped(text):
    b=io.BytesIO()
    with zipfile.ZipFile(b,"w") as z: z.writestr("sample.csv",text)
    return b.getvalue()

def test_checksum_download_cache_and_parse(tmp_path):
    body=zipped("1704067200000,100,102,99,101,20,1704067499999,2020,10,12,1212,0\n")
    digest=hashlib.sha256(body).hexdigest()
    calls=[]
    def transport(request):
        calls.append(str(request.url))
        return httpx.Response(200,text=digest+"  file.zip") if request.url.path.endswith("CHECKSUM") else httpx.Response(200,content=body)
    item=dict(family="candles",url="https://example.org/sample.zip",filename="sample.zip")
    (tmp_path/"raw").mkdir()
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        record=fetch(item,tmp_path,client)
        assert record["status"] == "verified"
        frame=parse(record,tmp_path)
        assert frame.close.iloc[0] == 101
        assert frame.available_at.iloc[0] == pd.Timestamp("2024-01-01T00:05:00Z")
        assert frame.taker_buy_volume.iloc[0] == 12
        before=len(calls)
        fetch(item,tmp_path,client)
        assert len(calls) == before+1  # Checksum is still checked; cached ZIP is not downloaded again.
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b"wrong"))) as client:
        assert fetch(item,tmp_path,client)["status"] == "missing"

def test_full_family_coverage_and_native_gap(monkeypatch):
    # Small period isolates coverage logic without a two-year fixture allocation.
    from quantpilot.data import archives as data
    start=pd.Timestamp("2024-01-01T00:00:00Z")
    end=start+pd.Timedelta(days=2)
    monkeypatch.setattr(data,"period",lambda as_of=None:(start,end,start))
    grid=pd.date_range(start,end,freq="5min",inclusive="left")
    frames={}
    for family,cols in VALUES.items():
        events=grid if family in ("candles","mark","index","positioning") else (
            pd.date_range(start,end,freq="8h",inclusive="left") if family == "funding" else
            pd.date_range(start-pd.Timedelta(days=1),end,freq="D",inclusive="left"))
        delay=pd.Timedelta(days=1) if family == "sentiment" else (
            pd.Timedelta(seconds=1) if family == "funding" else pd.Timedelta(minutes=5))
        d=pd.DataFrame(dict(event_time=events,available_at=events+delay,
                           received_at=end,source="https://public.example/data"))
        for col in cols: d[col]=.0001 if col=="funding_rate" else (50.0 if col=="fear_greed" else 100.0)
        if family == "candles":
            d["high"],d["low"],d["taker_buy_volume"]=101.,99.,40.
        frames[family]=d
    assert audit_frames(frames)["release_ready"]
    frames["positioning"]=frames["positioning"].drop(index=10)
    report=audit_frames(frames)
    assert not report["release_ready"]
    assert any("native" in issue for issue in report["families"]["positioning"]["issues"])



def test_funding_jitter_is_not_a_missing_settlement(monkeypatch):
    from quantpilot.data import archives as data
    start=pd.Timestamp("2024-01-01",tz="UTC")
    end=start+pd.Timedelta(days=2)
    monkeypatch.setattr(data,"period",lambda as_of=None:(start,end,start))
    times=pd.date_range(start,end,freq="8h",inclusive="left").to_series(index=range(6))
    times.iloc[1]+=pd.Timedelta(milliseconds=8)
    funding=pd.DataFrame(dict(event_time=times,available_at=times+pd.Timedelta(seconds=1),
                             received_at=end,source="https://public.example/funding",funding_rate=.0001))
    result=data.audit_frames({"funding":funding})["families"]["funding"]
    assert not any("settlement gap" in issue for issue in result["issues"])
    result=data.audit_frames({"funding":funding.drop(index=1)})["families"]["funding"]
    assert any("settlement gap" in issue for issue in result["issues"])


def test_invalid_positioning_is_quarantined_and_reported(monkeypatch):
    from quantpilot.data import archives as data
    start=pd.Timestamp("2024-01-01",tz="UTC")
    end=start+pd.Timedelta(hours=1)
    monkeypatch.setattr(data,"period",lambda as_of=None:(start,end,start))
    times=pd.date_range(start,end,freq="5min",inclusive="left")
    frame=pd.DataFrame(dict(event_time=times,available_at=times+pd.Timedelta(minutes=5),
                           received_at=end,source="https://public.example/metrics"))
    for column in VALUES["positioning"]: frame[column]=1.0
    frame.loc[3,"top_account_ratio"]=float("nan")
    frame=frame.drop(index=7)
    result=data.audit_frames({"positioning":frame})["families"]["positioning"]
    assert result["invalid_rows"] == 1
    assert result["invalid_columns"] == {"top_account_ratio":1}
    assert result["missing_windows"] == 1
    assert result["native_missing_count"] == 1
    assert result["native_gap_ranges"] == [[times[7].isoformat(),times[7].isoformat()]]
    assert result["invalid_ranges"] == [[times[3].isoformat(),times[3].isoformat()]]


def test_cached_audit_rejects_changed_parquet(tmp_path):
    import json
    from quantpilot.data.archives import audit_cached
    (tmp_path/"coverage.json").write_text(json.dumps({"dataset_hashes":{}}))
    (tmp_path/"candles.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError,match="Parquet hashes changed"):
        audit_cached(tmp_path,"2026-10-18")


def test_cached_audit_rechecks_raw_archive_hashes(tmp_path,monkeypatch):
    import json
    from quantpilot.data import archives as data
    body=b"verified archive"
    (tmp_path/"raw").mkdir()
    (tmp_path/"raw"/"example.zip").write_bytes(body)
    record=dict(filename="example.zip",family="candles",status="verified",sha256=hashlib.sha256(body).hexdigest())
    (tmp_path/"coverage.json").write_text(json.dumps(dict(dataset_hashes={},archives=[record])))
    monkeypatch.setattr(data,"manifest",lambda as_of=None:[dict(filename="example.zip",family="candles")])
    monkeypatch.setattr(data,"audit_frames",lambda *args:dict(release_ready=True))
    assert data.audit_cached(tmp_path)["release_ready"]
    (tmp_path/"raw"/"example.zip").write_bytes(b"tampered archive")
    result=data.audit_cached(tmp_path)
    assert not result["release_ready"]
    assert result["archives"][0]["status"] == "invalid"
