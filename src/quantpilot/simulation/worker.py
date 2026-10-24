"""Scheduled reconciliation and fresh-only paper entries. Owner pause keeps exits active."""
from contextlib import contextmanager
import time
import json
from filelock import FileLock
import pandas as pd
from sqlalchemy import text
from quantpilot.config import settings
from quantpilot.data.archives import utc, FUNDING_MAX_GAP
from quantpilot.simulation.engine import market_events, EVENT_PRIORITY
from quantpilot.data.live import ingest, live_quote
from quantpilot.storage.database import database, initialize, portfolio, apply_events
from quantpilot.api import services

@contextmanager
def worker_lock(engine):
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            acquired = connection.execute(text("SELECT pg_try_advisory_lock(71725002)")).scalar()
            if not acquired: raise RuntimeError("another worker is active")
            try: yield
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(71725002)"))
                connection.commit()
    else:
        with FileLock(str(settings.data_dir / "worker-run.lock"), timeout=1): yield

def run_worker():
    engine = database()
    initialize(engine)
    with worker_lock(engine):
        from quantpilot.monitoring.operations import begin_run,finish_run
        run_id=begin_run(engine,portfolio(engine))
        started=time.monotonic();progress={'stage':'ingestion'}
        try:
            state=_execute_worker(engine,progress)
            progress['stage']='model_metadata'
            version=services.model_version()
        except Exception as exc:
            finish_run(engine,run_id,'failed',progress['stage'],time.monotonic()-started,
                portfolio(engine),error_type=type(exc).__name__)
            raise
        finish_run(engine,run_id,'succeeded','complete',time.monotonic()-started,state,
            model_version=version)
        return state


def _execute_worker(engine,progress):
    """The caller holds the accounting worker lock throughout this operation."""
    state = portfolio(engine)
    frames = ingest(settings.data_dir / "live")
    progress['stage']='reconciliation'
    bars, events = reconciliation_events(frames, state["last_event"], pd.Timestamp.now(tz="UTC"))
    if events: apply_events(engine, events)
    now = pd.Timestamp.now(tz="UTC")
    ingestion_status = json.loads((settings.data_dir / "live" / "ingestion-status.json").read_text(encoding="utf-8"))
    progress['stage']='prediction'
    result = services.prediction()["data"]
    if ingestion_status["errors"]: result = {}
    if result.get("forecast"):
        event = dict(id=f"signal:{now.isoformat()}", kind="signal", time=now.isoformat(),forecast=result["forecast"])
        apply_events(engine, [event])
        progress['stage']='execution_quote'
        time.sleep(float(state["config"]["execution_delay_seconds"])+.1)
        q = live_quote()
        if utc(q["time"]) >= now:
            apply_events(engine, [dict(id=f"forward-quote:{q['time']}", kind="quote", **q)])
    else:
        state = portfolio(engine)
        observed = bars.available_at.max().isoformat()
        f = dict(probability=.5, up_magnitude=.001, down_magnitude=.001, atr=1,
                 observed_at=observed, model_version="unreleased", complete=False,funding_observable=False, funding_rate=0)
        apply_events(engine, [dict(id=f"signal:{now.isoformat()}", kind="signal",time=now.isoformat(), forecast=f)])
    progress['stage']='checkpoint'
    last_time = utc(portfolio(engine)["last_event"])
    end = max(pd.Timestamp.now(tz="UTC"), last_time+pd.Timedelta(microseconds=1)).isoformat()
    state = apply_events(engine, [dict(id=f"checkpoint:{end}", kind="checkpoint", time=end)])
    from quantpilot.storage.snapshots import write
    progress['stage']='snapshots'
    write(engine, {"candles":services.candles(2000), "indicators":services.indicators(),
                   "positioning":services.family("positioning"), "sentiment":services.family("sentiment"),
                   "predictions":services.prediction()})
    return state

def control(paused):
    engine = database()
    initialize(engine)
    now = pd.Timestamp.now(tz="UTC").isoformat()
    return apply_events(engine, [dict(id=f"control:{now}", kind="control", time=now, paused=paused)])


def reconciliation_events(frames, last_event=None, cutoff=None):
    """Validate market continuity before applying any accounting event."""
    bars = frames["candles"].sort_values("event_time").copy()
    if bars.empty:
        raise ValueError("no completed candles for reconciliation")
    if bars.event_time.duplicated().any():
        raise ValueError("duplicate reconciliation candles")
    if not (bars.available_at == bars.event_time+pd.Timedelta(minutes=5)).all():
        raise ValueError("invalid reconciliation candle duration")
    last = utc(last_event) if last_event else None
    relevant = bars if last is None else bars[bars.available_at > last]
    if last is not None and last < bars.event_time.min():
        raise ValueError("downtime exceeds retained market history; acquire missing bars before reconciliation")
    if not relevant.empty:
        if last is not None and relevant.event_time.iloc[0] > last:
            raise ValueError("market gap after reconciliation cursor")
        if (relevant.event_time.diff().dropna() != pd.Timedelta(minutes=5)).any():
            raise ValueError("missing reconciliation candles")
    marks = frames["mark"][["available_at","mark_price"]].sort_values("available_at")
    bars = pd.merge_asof(bars.sort_values("available_at"), marks, on="available_at",
                         direction="backward", tolerance=pd.Timedelta(minutes=10))
    if bars.mark_price.isna().any():
        raise ValueError("missing marks; reconciliation stopped")
    settlements = frames["funding"].sort_values("event_time")
    if settlements.empty or settlements.event_time.duplicated().any():
        raise ValueError("missing or duplicate funding settlement history")
    bar_end = utc(bars.available_at.max())
    end = utc(cutoff) if cutoff is not None else bar_end
    if end < bar_end or end-bar_end > pd.Timedelta(minutes=10):
        raise ValueError("invalid or stale reconciliation cutoff")
    # Cash-only bootstrap needs recent settlement history, not forty days of replay.
    first = last if last is not None else end
    relevant_funding = settlements[(settlements.event_time >= first-FUNDING_MAX_GAP) &
                                  (settlements.event_time <= end)]
    if relevant_funding.empty or relevant_funding.event_time.max() <= end-FUNDING_MAX_GAP:
        raise ValueError("stale funding settlement history")
    if relevant_funding.event_time.min() > first:
        raise ValueError("funding history does not cover reconciliation start")
    if relevant_funding.event_time.diff().max() > FUNDING_MAX_GAP:
        raise ValueError("funding settlement gap; reconciliation stopped")
    events = market_events(bars, settlements, replayed=True)
    # Funding can settle milliseconds after the latest candle close. Include it
    # before advancing the worker checkpoint, using only the last observable mark.
    tail = settlements[(settlements.event_time > bar_end) & (settlements.event_time <= end)]
    for row in tail.itertuples():
        t = row.event_time.isoformat()
        events.append(dict(id=f"funding:{t}", time=t, kind="funding", rate=float(row.funding_rate),
                           mark=float(bars.mark_price.iloc[-1]), replayed=True))
    events.sort(key=lambda event:(utc(event["time"]), EVENT_PRIORITY[event["kind"]]))
    events = [e for e in events if utc(e["time"]) > last] if last is not None else events[-1:]
    return bars, events


