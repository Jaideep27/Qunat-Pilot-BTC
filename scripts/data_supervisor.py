"""Local continuous collector, resumable history, and serialized paper updates."""
from concurrent.futures import ThreadPoolExecutor
import time
import os
import json
import pandas as pd
from filelock import FileLock, Timeout
from quantpilot.collector import run as collect
from quantpilot.history import backfill
from quantpilot.worker import run_worker
from quantpilot.config import settings
from quantpilot.store import database, initialize
from quantpilot.market_store import feed_status
from quantpilot.operations import next_paper_run
from quantpilot import forecaster, macro, news
from quantpilot.process import process_started


def repeating_history():
    engine=database(settings.market_database_url)
    while True:
        try:backfill()
        except Exception as exc:feed_status(engine,'history:overall',dict(status='blocked',error=str(exc)))
        time.sleep(1800)


def news_feeds():
    # Feed collection never waits on reasoning; Codex runs at most once per analysis interval.
    # Rule engine news layer: article text (isolated Crawl4AI) and impact classification every 15 minutes.
    from quantpilot import news_events
    last_events=None
    while True:
        try:news.run(reasoning=True)
        except Exception as exc:print(json.dumps(dict(news='blocked',error_type=type(exc).__name__)),flush=True)
        if last_events is None or time.monotonic()-last_events>=900:
            try:print(json.dumps(dict(news_events=news_events.cycle())),flush=True)
            except Exception as exc:print(json.dumps(dict(news_events='blocked',error_type=type(exc).__name__)),flush=True)
            last_events=time.monotonic()
        time.sleep(settings.news_poll_seconds)


def macro_calendar():
    # Official schedules change rarely; refresh daily and keep first-release values once stored.
    while True:
        try:macro.run()
        except Exception as exc:print(json.dumps(dict(macro='blocked',error_type=type(exc).__name__)),flush=True)
        time.sleep(3600)


def shadow_forecasts():
    # Research forecasts every 15 minutes (minutes 7/22/37/52, after the paper worker); refits once a day.
    engine=database()
    while True:
        try:print(json.dumps(dict(forecasts=forecaster.run_cycle(engine)),default=str),flush=True)
        except Exception as exc:print(json.dumps(dict(forecasts='blocked',error_type=type(exc).__name__)),flush=True)
        now=pd.Timestamp.now(tz='UTC');target=now.floor('15min')+pd.Timedelta(minutes=7)
        time.sleep(max(1,((target if target>now else target+pd.Timedelta(minutes=15))-now).total_seconds()))


def outlook_cycle():
    # Hourly panel refresh + scheduled questions; scoring every 15 minutes.
    from quantpilot import answers, services
    from quantpilot.event_study import load_market
    from quantpilot.outlook import refresh_panel
    engine, last_refresh = database(), None
    while True:
        try:
            now = pd.Timestamp.now(tz='UTC')
            if last_refresh is None or now-last_refresh >= pd.Timedelta(hours=1):
                refresh_panel(now)
                last_refresh = now
                answers.scheduled_cycle(engine, lambda text, ai, source: services.ask(text, ai=ai, source=source), now)
            recent = load_market(database(settings.market_database_url), now-pd.Timedelta(days=8), now)['candles']
            bars = recent.set_index('available_at')[['high', 'low', 'close']].astype(float)
            print(json.dumps(dict(outlook=dict(scored=answers.evaluate(engine, bars, now)))), flush=True)
        except Exception as exc:
            print(json.dumps(dict(outlook='blocked', error_type=type(exc).__name__)), flush=True)
        time.sleep(900)


def forward_research():
    # Liquidations (stream) and order-book depth (1/min) exist only if recorded as they happen.
    from quantpilot import forward_data
    while True:
        try:forward_data.run()
        except Exception as exc:print(json.dumps(dict(forward_research='blocked',error_type=type(exc).__name__)),flush=True)
        time.sleep(60)


def rule_engine():
    # Rule engine: one cycle a minute (5 s past), the experimental futures-v1 paper account from rulebook v1.
    import httpx
    from quantpilot import rule_live, layers
    engine=database(settings.market_database_url)
    with httpx.Client(timeout=15) as client:
        while True:
            try:
                out=rule_live.cycle(engine,client=client,layers=layers.live_layers(),modes=layers.modes(),
                                    macro_events=macro.events(database()),news_events=layers.news_events())
                print(json.dumps(dict(rules=dict(bars=out['bars'],triggers=out['triggers'],equity=out['equity'],
                    action=(out['decision'] or {}).get('action'),diagnosed=out['diagnosed']))),flush=True)
            except Exception as exc:
                print(json.dumps(dict(rules='blocked',error_type=type(exc).__name__,error=str(exc)[:200])),flush=True)
                try:feed_status(engine,'rules:engine',dict(status='blocked',error=str(exc)[:500]))
                except Exception:pass
            now=pd.Timestamp.now(tz='UTC');target=now.floor('min')+pd.Timedelta(seconds=65)
            time.sleep(max(1,(target-now).total_seconds()))


def paper():
    engine=database(settings.market_database_url)
    while True:
        try:
            state=run_worker()
            feed_status(engine,'paper:worker',dict(status='healthy',last_checkpoint=state.get('last_worker')))
        except Exception as exc:feed_status(engine,'paper:worker',dict(status='blocked',error=str(exc)))
        time.sleep(max(1,(next_paper_run()-pd.Timestamp.now(tz='UTC')).total_seconds()))


if __name__=='__main__':
    settings.data_dir.mkdir(exist_ok=True,parents=True)
    try:
        with FileLock(str(settings.data_dir/'data-supervisor.lock'),timeout=0):
            (settings.data_dir/'data-supervisor-runtime.json').write_text(json.dumps(dict(pid=os.getpid(),started=process_started())),encoding="utf-8")
            initialize(database(settings.market_database_url))
            initialize(database())
            with ThreadPoolExecutor(max_workers=9) as pool:
                futures=[pool.submit(collect),pool.submit(repeating_history),pool.submit(paper),pool.submit(news_feeds),pool.submit(macro_calendar),pool.submit(shadow_forecasts),pool.submit(outlook_cycle),pool.submit(forward_research),pool.submit(rule_engine)]
                for future in futures:future.result()
    except Timeout:
        print('A data supervisor is already running.',flush=True)
