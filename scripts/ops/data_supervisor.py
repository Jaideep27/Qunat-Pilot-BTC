"""The background services, one thread each: market data, news, the macro calendar, forecasts, the outlook and the
analysis snapshot with Claude's brief. Nothing here trades (paper trading was removed on 2026-11-02, A15)."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import time
import pandas as pd
from filelock import FileLock, Timeout
from quantpilot.ai import news
from quantpilot.config import settings
from quantpilot.data import macro
from quantpilot.data.collector import run as collect
from quantpilot.data.history import backfill
from quantpilot.monitoring.process import process_started
from quantpilot.research import forecaster
from quantpilot.storage.database import database, initialize
from quantpilot.storage.market import feed_status


def log(**fields):
    print(json.dumps(fields, default=str), flush=True)


def every(seconds, job, name):
    """Run `job` forever, `seconds` apart; a failure is logged and never stops the thread."""
    while True:
        try:
            job()
        except Exception as exc:
            log(**{name: 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)[:200]})
        time.sleep(seconds)


def history():
    # Resumable five-year backfill; fills any gap the live collector missed. Failures show on the System page.
    engine = database(settings.market_database_url)
    while True:
        try:
            backfill()
        except Exception as exc:
            feed_status(engine, 'history:overall', dict(status='blocked', error=str(exc)))
        time.sleep(1800)


def news_feeds():
    # Feed collection never waits on reasoning; the AI analysis runs at most once per analysis interval.
    every(settings.news_poll_seconds, lambda: news.run(reasoning=True), 'news')


def macro_calendar():
    # Official schedules change rarely; first-release values are kept once stored.
    every(3600, macro.run, 'macro')


def forward_research():
    # Liquidations (stream) and order-book depth (1/min) exist only if recorded as they happen.
    from quantpilot.data import forward
    every(60, forward.run, 'forward_research')


def shadow_forecasts():
    # Research forecasts every 15 minutes (minute 7 of each quarter hour); refits once a day.
    engine = database()
    while True:
        try:
            log(forecasts=forecaster.run_cycle(engine))
        except Exception as exc:
            log(forecasts='blocked', error_type=type(exc).__name__)
        now = pd.Timestamp.now(tz='UTC')
        target = now.floor('15min')+pd.Timedelta(minutes=7)
        time.sleep(max(1, ((target if target > now else target+pd.Timedelta(minutes=15))-now).total_seconds()))


def outlook_cycle():
    # Hourly panel refresh and scheduled questions; answers are scored every 15 minutes.
    from quantpilot.api import services
    from quantpilot.outlook import answers
    from quantpilot.outlook.engine import refresh_panel
    from quantpilot.research.event_study import load_market
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
            log(outlook=dict(scored=answers.evaluate(engine, bars, now)))
        except Exception as exc:
            log(outlook='blocked', error_type=type(exc).__name__)
        time.sleep(900)


def analysis():
    # The analysis snapshot a minute past every 5m close, then Claude's brief when one is due.
    from quantpilot.analysis import ai, snapshot
    engine = database(settings.market_database_url)
    while True:
        try:
            snap = snapshot.build(engine)
            snapshot.save(engine, snap)
            feed_status(engine, 'analysis:snapshot', dict(status='healthy', built_at=snap['built_at'], data_time=snap['data_time']))
            reason = ai.due(engine, snap)
            brief = ai.write_brief(engine, snap, reason=reason) if reason else None
            log(analysis=snap['built_at'], brief=brief and brief['status'])
        except Exception as exc:
            log(analysis='blocked', error_type=type(exc).__name__, error=str(exc)[:200])
            try:
                feed_status(engine, 'analysis:snapshot', dict(status='blocked', error=str(exc)[:500]))
            except Exception:
                pass
        now = pd.Timestamp.now(tz='UTC')
        time.sleep(max(30, (now.floor('5min')+pd.Timedelta(minutes=6)-now).total_seconds()))


JOBS = [collect, history, news_feeds, macro_calendar, forward_research, shadow_forecasts, outlook_cycle, analysis]


if __name__ == '__main__':
    settings.data_dir.mkdir(exist_ok=True, parents=True)
    try:
        with FileLock(str(settings.data_dir/'data-supervisor.lock'), timeout=0):
            (settings.data_dir/'data-supervisor-runtime.json').write_text(
                json.dumps(dict(pid=os.getpid(), started=process_started())), encoding='utf-8')
            initialize(database(settings.market_database_url))
            initialize(database())
            with ThreadPoolExecutor(max_workers=len(JOBS)) as pool:
                for future in [pool.submit(job) for job in JOBS]:
                    future.result()
    except Timeout:
        print('A data supervisor is already running.', flush=True)
