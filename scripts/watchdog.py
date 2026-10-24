"""Watchdog (Task Scheduler, every 5 minutes and at logon): look at the system, restart stuck services, email alerts.

One look per run; the decisions are in `quantpilot.ops.tick`. State lives in data/ops/state.json, one line per run
goes to data/ops/watchdog.log, and every alert is also written to data/ops/alerts.log. Restarts always redirect
their output to files (unredirected launches died on 2026-10-23). Nothing here touches trading.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from quantpilot.monitoring import ops  # noqa: E402
from quantpilot.config import settings  # noqa: E402

OPS = ROOT/'data'/'ops'
LOG_LIMIT = 1_000_000


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _append(path, line):
    path = Path(path)
    if path.exists() and path.stat().st_size > LOG_LIMIT:      # keep the newest half
        text = path.read_text(encoding='utf-8')
        path.write_text(text[len(text)//2:], encoding='utf-8')
    with open(path, 'a', encoding='utf-8') as f:
        f.write(line.rstrip('\n')+'\n')


def probes(state, now):
    import httpx
    p = dict(ready=None, engine_at=None, candle_age_s=None, model=None)
    try:
        ready = httpx.get('http://127.0.0.1:8000/api/v1/ready', timeout=10).json()['data']
        p['ready'] = ready
        p['candle_age_s'] = ((ready.get('checks') or {}).get('completed_candles') or {}).get('age_seconds')
    except Exception:
        pass
    try:
        from sqlalchemy import select
        from quantpilot.storage.market import MarketFeed
        from quantpilot.storage.database import database
        engine = database(settings.market_database_url)
        with engine.connect() as connection:
            feeds = dict(connection.execute(select(MarketFeed.id, MarketFeed.updated_at)
                                            .where(MarketFeed.id.in_(['rules:engine', 'rules:model-health']))).all())
            health = connection.execute(select(MarketFeed.state).where(MarketFeed.id == 'rules:model-health')).scalar()
        engine.dispose()
        if feeds.get('rules:engine') is not None:
            t = pd.Timestamp(feeds['rules:engine'])
            p['engine_at'] = t.tz_localize('UTC') if t.tzinfo is None else t
        p['model'] = (health or {}).get('overall')
    except Exception:
        pass
    p['disk_gb'] = shutil.disk_usage(ROOT).free/1e9
    backup = _read(OPS/'backup.json', {})
    p['backup_at'] = pd.Timestamp(backup.get('last_ok') or state.setdefault('first_run', now.isoformat()))
    return p


def restart(now):
    logs = ROOT/'.verification'/'services'
    logs.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime('%Y%m%d-%H%M%S')
    node = shutil.which('node') or 'C:/Program Files/nodejs/node.exe'
    with open(logs/f'watchdog-restart-{stamp}.out.log', 'w') as out, open(logs/f'watchdog-restart-{stamp}.err.log', 'w') as err:
        subprocess.Popen([node, 'scripts/start.cjs', '--restart-services'], cwd=ROOT, stdout=out, stderr=err,
                         stdin=subprocess.DEVNULL, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def main():
    OPS.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz='UTC')
    state = _read(OPS/'state.json', {})
    p = probes(state, now)
    problems = ops.check(p, now)
    first_run = state.get('first_run')
    state, actions = ops.tick(state, problems, now)
    state['first_run'] = first_run or now.isoformat()
    done = []
    for action in actions:
        if action[0] == 'restart':
            try:
                restart(now)
                done.append('restart')
            except Exception as exc:
                done.append(f'restart failed: {type(exc).__name__}')
        elif action[0] == 'email':
            _, subject, body = action
            ok, error = ops.send_email(subject, body, dict(email=settings.alert_email, password=settings.alert_app_password))
            _append(OPS/'alerts.log', f"{now.isoformat()} {subject} | {body.splitlines()[0]} | email {'sent' if ok else error}")
            done.append('email sent' if ok else 'email not sent')
    (OPS/'state.json').write_text(json.dumps(state, indent=1), encoding='utf-8')
    _append(OPS/'watchdog.log', json.dumps(dict(time=now.isoformat(), problems=[x['key'] for x in problems], actions=done)))
    print(json.dumps(dict(problems=[x['text'] for x in problems], actions=done)))


if __name__ == '__main__':
    main()
