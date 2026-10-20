"""Local continuous collector, resumable history, and serialized paper updates."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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
    while True:
        try:news.run(reasoning=True)
        except Exception as exc:print(json.dumps(dict(news='blocked',error_type=type(exc).__name__)),flush=True)
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
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures=[pool.submit(collect),pool.submit(repeating_history),pool.submit(paper),pool.submit(news_feeds),pool.submit(macro_calendar),pool.submit(shadow_forecasts),pool.submit(outlook_cycle),pool.submit(forward_research)]
                for future in futures:future.result()
    except Timeout:
        print('A data supervisor is already running.',flush=True)
