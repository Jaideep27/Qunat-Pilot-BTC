"""Nightly backup (Task Scheduler, 02:30): PostgreSQL + SQLite to data/backups, latest 2 copied to OneDrive.

The result goes to data/ops/backup.json (read by the watchdog) and data/ops/backup.log; a failure is emailed.
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))

from quantpilot.monitoring import ops  # noqa: E402
from quantpilot.config import settings  # noqa: E402

PG_BIN = Path('C:/Program Files/PostgreSQL/18/bin')


def main():
    ops_dir = ROOT/'data'/'ops'
    ops_dir.mkdir(parents=True, exist_ok=True)
    # All live data is in PostgreSQL; a SQLite app database is copied only when it is the configured one.
    sqlite = settings.database_url.removeprefix('sqlite:///') if settings.database_url.startswith('sqlite:///') else None
    now = pd.Timestamp.now(tz='UTC')
    result = ops.backup(now, PG_BIN, settings.market_database_url, sqlite and (ROOT/sqlite if not Path(sqlite).is_absolute() else sqlite),
                        ROOT/'data'/'backups', Path.home()/'OneDrive'/'QuantPilot-backups')
    previous = {}
    try:
        previous = json.loads((ops_dir/'backup.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    record = dict(result, last_ok=result['time'] if result['ok'] else previous.get('last_ok'))
    (ops_dir/'backup.json').write_text(json.dumps(record, indent=1, default=str), encoding='utf-8')
    with open(ops_dir/'backup.log', 'a', encoding='utf-8') as log:
        log.write(json.dumps(dict(time=result['time'], ok=result['ok'], size_mb=result['size_mb'], offsite=result['offsite'],
                                  error=result['error']))+'\n')
    if not result['ok'] or result['offsite'] != 'copied':
        problem = result['error'] or f"the OneDrive copy was not made ({result['offsite']})"
        ok, error = ops.send_email('QuantPilot: backup problem', f'Tonight\'s backup had a problem: {problem}\n'
                                   'Older backups are kept. Paper trading only.\n',
                                   dict(email=settings.alert_email, password=settings.alert_app_password))
        if not ok:
            with open(ops_dir/'alerts.log', 'a', encoding='utf-8') as log:
                log.write(f'{now.isoformat()} backup problem: {problem} (email: {error})\n')
    print(json.dumps(dict(ok=result['ok'], size_mb=result['size_mb'], offsite=result['offsite'], error=result['error'])))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
