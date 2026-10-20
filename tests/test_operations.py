import pandas as pd
import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient
from quantpilot import worker,services
from quantpilot.operations import WorkerRun,begin_run,finish_run,report,next_paper_run,ledger_evidence
from quantpilot.store import portfolio,apply_events,LedgerEvent
from quantpilot.market_store import save_frame
from quantpilot.api import app


def test_schedule_aligns_to_utc_quarter_hours_without_backdating():
    assert next_paper_run('2026-10-19T10:01:59Z')==pd.Timestamp('2026-10-19T10:02:00Z')
    assert next_paper_run('2026-10-19T10:02:00Z')==pd.Timestamp('2026-10-19T10:17:00Z')
    assert next_paper_run('2026-10-19T23:59:00Z')==pd.Timestamp('2026-10-20T00:02:00Z')


def test_interrupted_run_is_detected_without_replaying_or_creating_accounting_events(account):
    before=portfolio(account)
    first=begin_run(account,before,now='2026-01-01T00:00:00Z')
    second=begin_run(account,before,now='2026-01-01T00:17:00Z')
    with account.connect() as connection:
        rows={row['id']:dict(row) for row in connection.execute(select(WorkerRun.__table__)).mappings()}
        assert not connection.execute(select(LedgerEvent.id)).all()
    assert rows[first]['status']=='interrupted' and rows[first]['duration_seconds'] is None
    assert rows[second]['status']=='running' and portfolio(account)==before


def test_worker_records_ingestion_failure_without_public_secret_messages(account,monkeypatch):
    def unavailable(root):raise ConnectionError('postgresql://private:secret@example.com/db')
    monkeypatch.setattr(worker,'ingest',unavailable)
    with pytest.raises(ConnectionError):worker.run_worker()
    status=report(account)
    run=status['worker']['runs'][0]
    assert status['worker']['status']=='failed' and not status['ready']
    assert run['stage']=='ingestion' and run['error_type']=='ConnectionError'
    assert 'secret' not in str(status) and not status['forward_evidence']['forward_decisions']
    assert portfolio(account)['last_worker'] is None


def test_successful_worker_records_checkpoint_and_durations_with_no_forced_trade(account,monkeypatch):
    # Exercise the real no-model, no-trade worker path with isolated ingestion.
    now=pd.Timestamp.now(tz='UTC').floor('5min')
    bars=pd.DataFrame(dict(event_time=[now-pd.Timedelta(minutes=5)],available_at=[now],
        open=[100.],high=[101.],low=[99.],close=[100.]))
    frames={'candles':bars,'mark':bars[['available_at']].assign(mark_price=100.),
        'funding':pd.DataFrame(dict(event_time=[now-pd.Timedelta(hours=1)],funding_rate=[.0001]))}
    def ingest(root):
        root.mkdir(parents=True,exist_ok=True)
        (root/'ingestion-status.json').write_text('{"errors":[]}',encoding='utf-8')
        return frames
    monkeypatch.setattr(worker,'ingest',ingest)
    monkeypatch.setattr(services,'prediction',lambda:{'data':{}})
    monkeypatch.setattr(services,'candles',lambda *args:{'data':[]})
    monkeypatch.setattr(services,'indicators',lambda:{'data':{}})
    monkeypatch.setattr(services,'family',lambda *args:{'data':[]})
    monkeypatch.setattr(services,'model_version',lambda:None)
    result=worker.run_worker()
    status=report(account)
    assert result['last_worker'] and not result['fills']
    assert status['worker']['status']=='healthy'
    assert status['worker']['run_totals']=={'succeeded':1}
    assert status['worker']['duration_p95_seconds']>=0
    assert status['worker']['runs'][0]['details']['action']=='NO_TRADE'
    assert status['forward_evidence']['forward_decisions']==1


def test_new_failure_overrides_fresh_checkpoint_and_success_clears_it(account):
    checkpoint=pd.Timestamp.now(tz='UTC')-pd.Timedelta(minutes=1)
    state=apply_events(account,[dict(id='first-checkpoint',kind='checkpoint',time=checkpoint.isoformat())])
    run=begin_run(account,state)
    finish_run(account,run,'failed','execution_quote',2.,state,error_type='TimeoutError')
    assert report(account)['worker']['status']=='failed'
    recovered=begin_run(account,state)
    state=apply_events(account,[dict(id='recovered-checkpoint',kind='checkpoint',time=pd.Timestamp.now(tz='UTC').isoformat())])
    finish_run(account,recovered,'succeeded','complete',3.,state)
    assert report(account)['worker']['status']=='healthy'


def test_readiness_requires_actual_freshness_and_returns_503_for_stale_worker(account,monkeypatch):
    now=pd.Timestamp.now(tz='UTC')
    bar=now.floor('5min')-pd.Timedelta(minutes=5)
    save_frame(account,'candles',pd.DataFrame([dict(event_time=bar,available_at=bar+pd.Timedelta(minutes=5),
        received_at=now,source='fixture',open=100,high=102,low=99,close=101,volume=1)]))
    state=apply_events(account,[dict(id='fresh-signal',kind='signal',time=now.isoformat(),forecast=dict(
        probability=.5,up_magnitude=.001,down_magnitude=.001,atr=1,observed_at=now.isoformat(),
        model_version='fixture-model',funding_observable=True,funding_rate=0)),
        dict(id='fresh-checkpoint',kind='checkpoint',time=now.isoformat())])
    run=begin_run(account,state);finish_run(account,run,'succeeded','complete',1.,state)
    monkeypatch.setattr(services,'coverage',lambda:{'data':{'release_ready':True}})
    monkeypatch.setattr(services,'model_version',lambda:'fixture-model')
    with TestClient(app) as client:
        assert client.get('/api/v1/ready').status_code==200
        assert client.get('/api/v1/operations?limit=101').status_code==422
        assert client.post('/api/v1/operations').status_code==405
        actual=client.get('/api/v1/operations').json()['data']
        expected=services.operations()['data']
        assert actual['forward_evidence']==expected['forward_evidence']
        assert actual['worker']['runs']==expected['worker']['runs']
    later=report(account,now=now+pd.Timedelta(hours=1),model_version='fixture-model',coverage_ready=True)
    assert later['worker']['status']=='stale' and not later['ready']
    monkeypatch.setattr(services,'operations',lambda limit=20:services.envelope(later))
    with TestClient(app) as client:
        assert client.get('/api/v1/ready').status_code==503
        assert client.get('/api/v1/health').json()['data']['status']=='ok'
    with pytest.raises(ValueError):report(account,limit=101)


def test_forward_evidence_uses_immutable_history_and_separates_replayed_fills(account):
    from quantpilot.store import serialized
    with serialized(account) as session:
        for i in range(3):
            session.add(LedgerEvent(id=f'fixture:{i}',event_time=f'2026-01-01T00:0{i}:00Z',payload={
                'kind':'signal','replayed':i==2,'effects':{'fills':[{'replayed':i==2}],'decisions':[]}}))
    evidence=ledger_evidence(account)
    assert evidence['forward_decisions']==2 and evidence['forward_fills']==2 and evidence['replayed_fills']==1


def test_database_failure_is_a_safe_503_not_a_healthy_readiness_result(account,monkeypatch):
    from sqlalchemy.exc import OperationalError
    def unavailable(*args):raise OperationalError('SELECT',{},Exception('private connection secret'))
    monkeypatch.setattr(services,'operations',unavailable)
    with TestClient(app) as client:
        for path in ('/api/v1/operations','/api/v1/ready'):
            result=client.get(path)
            assert result.status_code==503 and 'secret' not in result.text
