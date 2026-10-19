"""Persistent worker-run evidence and read-only operational readiness."""
import uuid
import numpy as np
import pandas as pd
from sqlalchemy import Column,String,DateTime,Float,JSON,select,update,func
from .store import Base,LedgerEvent,portfolio
from .market_store import MarketCandle,MarketFeed,PERP
from .config import settings,RiskConfig
from .data import utc


class WorkerRun(Base):
    __tablename__='worker_runs'
    id=Column(String(32),primary_key=True)
    started_at=Column(DateTime(timezone=True),nullable=False,index=True)
    finished_at=Column(DateTime(timezone=True))
    status=Column(String(24),nullable=False,index=True)
    stage=Column(String(32),nullable=False)
    duration_seconds=Column(Float)
    error_type=Column(String(120))
    details=Column(JSON,nullable=False)


def next_paper_run(now=None):
    now=utc(now or pd.Timestamp.now(tz='UTC'))
    target=now.floor('15min')+pd.Timedelta(minutes=2)
    return target if target>now else target+pd.Timedelta(minutes=15)


def begin_run(engine,state,now=None):
    """Caller holds the paper worker lock, so surviving running rows are interrupted."""
    now=utc(now or pd.Timestamp.now(tz='UTC'))
    run_id=uuid.uuid4().hex
    with engine.begin() as connection:
        connection.execute(update(WorkerRun).where(WorkerRun.status=='running').values(
            status='interrupted',stage='restart_detected',finished_at=now.to_pydatetime(),
            error_type='PreviousProcessInterrupted'))
        connection.execute(WorkerRun.__table__.insert().values(id=run_id,started_at=now.to_pydatetime(),
            status='running',stage='ingestion',details={'checkpoint_before':state.get('last_event'),
                'config_version':RiskConfig(**state['config']).identity}))
    return run_id


def finish_run(engine,run_id,status,stage,duration,state,error_type=None,model_version=None):
    now=pd.Timestamp.now(tz='UTC')
    with engine.begin() as connection:
        original=connection.execute(select(WorkerRun.__table__).where(WorkerRun.id==run_id)).mappings().one()
        decision=(state.get('decisions') or [{}])[-1]
        # Never attribute an earlier decision to a failed/new worker run.
        current=decision if decision.get('time') and utc(decision['time'])>=utc(original['started_at']) else {}
        details={**original['details'],'checkpoint_after':state.get('last_event'),
            'worker_checkpoint':state.get('last_worker'),'equity':state['equity'],
            'model_version':model_version,'action':current.get('action'),'reason_codes':current.get('reasons',[])}
        connection.execute(update(WorkerRun).where(WorkerRun.id==run_id).values(status=status,
            finished_at=now.to_pydatetime(),stage=stage,duration_seconds=max(0.,duration),
            error_type=error_type,details=details))


def ledger_evidence(engine):
    """Read all immutable ledger rows; bounded dashboard arrays are not lifetime totals."""
    parent_replayed=LedgerEvent.payload['replayed'].as_boolean().is_(True)
    signal=(LedgerEvent.payload['kind'].as_string()=='signal') & ~parent_replayed
    with engine.connect() as connection:
        decisions=connection.execute(select(func.count(),func.min(LedgerEvent.event_time),
            func.max(LedgerEvent.event_time)).where(signal)).one()
        fills=func.coalesce(func.sum(func.json_array_length(LedgerEvent.payload['effects']['fills'])),0)
        forward=int(connection.execute(select(fills).where(~parent_replayed)).scalar_one())
        replayed=int(connection.execute(select(fills).where(parent_replayed)).scalar_one())
    return dict(forward_decisions=int(decisions[0]),first_forward_decision=decisions[1],
        last_forward_decision=decisions[2],forward_fills=forward,replayed_fills=replayed,
        note='Counts come from the immutable ledger. Observation span is not an uptime percentage.')


def report(engine,market_engine=None,limit=20,now=None,model_version=None,coverage_ready=False):
    if not 1<=limit<=100:raise ValueError('operations limit must be between 1 and 100')
    now=utc(now or pd.Timestamp.now(tz='UTC'));market_engine=market_engine or engine
    state=portfolio(engine)
    def age(value):return max(0.,(now-utc(value)).total_seconds()) if value else None
    with engine.connect() as connection:
        recent=connection.execute(select(WorkerRun.__table__).order_by(WorkerRun.started_at.desc()).limit(100)).mappings().all()
        totals=dict(connection.execute(select(WorkerRun.status,func.count()).group_by(WorkerRun.status)).all())
    with market_engine.connect() as connection:
        last_bar=connection.execute(select(func.max(MarketCandle.available_at)).where(
            MarketCandle.market==PERP,MarketCandle.complete.is_(True),MarketCandle.available_at<=now.to_pydatetime())).scalar()
        feed=connection.execute(select(MarketFeed.__table__).where(MarketFeed.id=='live:collector')).mappings().first()
    checkpoint_age=age(state.get('last_worker'));latest=recent[0] if recent else None
    worker='never_run' if checkpoint_age is None else ('healthy' if checkpoint_age<=settings.worker_max_age_seconds else 'stale')
    if latest and latest['status']=='running':
        worker='running' if age(latest['started_at'])<=settings.worker_max_age_seconds else 'stalled'
    elif latest and latest['status']=='failed' and (not state.get('last_worker') or utc(latest['finished_at'])>utc(state['last_worker'])):
        worker='failed'
    if state.get('last_worker') and utc(state['last_worker'])>now+pd.Timedelta(seconds=30):worker='invalid_clock'
    candle_age=age(last_bar)
    last_decision=(state.get('decisions') or [{}])[-1]
    data_blocks={'MISSING_CRITICAL_INPUT','STALE_DATA','INVALID_FORECAST','MODEL_RELEASE_BLOCKED','MODEL_SCHEMA_MISMATCH'}
    inputs_ready=bool(last_decision) and not bool(data_blocks.intersection(last_decision.get('reasons',[])))
    checks={
        'database':dict(ok=True,status='connected'),
        'completed_candles':dict(ok=candle_age is not None and candle_age<=RiskConfig().max_age_seconds,
            status='missing' if candle_age is None else ('fresh' if candle_age<=RiskConfig().max_age_seconds else 'stale'),age_seconds=candle_age),
        'paper_worker':dict(ok=checkpoint_age is not None and checkpoint_age<=settings.worker_max_age_seconds and worker in ('healthy','running'),status=worker,age_seconds=checkpoint_age),
        'run_monitoring':dict(ok=bool(recent),status='recording' if recent else 'not_started'),
        'model':dict(ok=bool(model_version),status='released' if model_version else 'unreleased'),
        'coverage':dict(ok=bool(coverage_ready),status='eligible' if coverage_ready else 'blocked')}
    checks['prediction_inputs']=dict(ok=inputs_ready,status='observed' if inputs_ready else 'unavailable_or_blocked')
    durations=[row['duration_seconds'] for row in recent if row['status']=='succeeded' and row['duration_seconds'] is not None]
    runs=[]
    for row in recent[:limit]:
        item=dict(row)
        for key in ('started_at','finished_at'):item[key]=utc(item[key]).isoformat() if item[key] else None
        runs.append(item)
    evidence=ledger_evidence(engine)
    evidence['observed_span_hours']=max(0.,(utc(state['last_worker'])-utc(evidence['first_forward_decision'])).total_seconds()/3600) if state.get('last_worker') and evidence['first_forward_decision'] else 0.
    return dict(ready=all(check['ok'] for check in checks.values()),checked_at=now.isoformat(),checks=checks,
        worker=dict(status=worker,last_checkpoint=state.get('last_worker'),age_seconds=checkpoint_age,
            max_age_seconds=settings.worker_max_age_seconds,next_scheduled_at=next_paper_run(now).isoformat(),
            schedule_utc='2,17,32,47 * * * *',runs=runs,run_totals=totals,
            latency_sample_count=len(durations),latency_sample_scope='Latest 100 recorded runs; successful runs only',
            duration_p50_seconds=float(np.percentile(durations,50)) if durations else None,
            duration_p95_seconds=float(np.percentile(durations,95)) if durations else None),
        collector=dict(status=feed['state'].get('status') if feed else 'not_recorded',
            age_seconds=age(feed['updated_at']) if feed else None,
            poll_interval_seconds=feed['state'].get('poll_interval_seconds') if feed else None),
        forward_evidence=evidence)
