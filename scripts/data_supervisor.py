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


def repeating_history():
    engine=database(settings.market_database_url)
    while True:
        try:backfill()
        except Exception as exc:feed_status(engine,'history:overall',dict(status='blocked',error=str(exc)))
        time.sleep(1800)


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
            (settings.data_dir/'data-supervisor-runtime.json').write_text(json.dumps(dict(pid=os.getpid(),started=time.time())),encoding='utf-8')
            initialize(database(settings.market_database_url))
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures=[pool.submit(collect),pool.submit(repeating_history),pool.submit(paper)]
                for future in futures:future.result()
    except Timeout:
        print('A data supervisor is already running.',flush=True)
