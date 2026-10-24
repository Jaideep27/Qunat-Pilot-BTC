# Operations: watchdog, email alerts and backups

Built 2026-10-23. Design: `docs/superpowers/specs/2026-10-23-ops-safety-design.md`. Nothing here touches trading.

## What runs
Two Windows Task Scheduler tasks run under the folder `\QuantPilot\`, as the owner, while the owner is logged on. They use `pythonw.exe`, so no window appears.

### QuantPilot Watchdog
Runs every 5 minutes and at logon (`scripts/ops/watchdog.py`).

**What it checks:**
- the API (`/api/v1/ready`);
- the engine heartbeat (feed `rules:engine` must be under 5 minutes old);
- market data age (alert if over 20 minutes);
- model health (alert on Alarm);
- free disk (alert under 5 GB);
- backup age (alert over 36 hours).

**When it acts:**
- A problem counts only when it is seen on two runs in a row.
- If the API or the engine is down, it runs `node scripts/start.cjs --restart-services`, with its output in `.verification/services/watchdog-restart-*.log`.
- It restarts at most 3 times in 2 hours. After that it emails that it gave up, and tries again once the 2-hour window frees up.
- It emails when a problem starts, every 6 hours while the problem lasts, and when it is resolved.

### QuantPilot Backup
Runs daily at 02:30 (`scripts/ops/backup.py`). If the laptop was asleep, it runs as soon as possible afterwards.
- It runs `pg_dump` of the database to `data/backups/quantpilot-pg-YYYYMMDD-HHMM.dump`, about 90 MB.
- Each dump is checked with `pg_restore --list` before it counts.
- 7 backups are kept on the laptop, and the latest 2 are copied to `OneDrive\QuantPilot-backups`.
- A failure, or a missing OneDrive copy, is emailed. Older backups are never deleted on failure.

## Files

| File | What it is |
|---|---|
| `data/ops/state.json` | the watchdog's memory: active problems, restarts |
| `data/ops/watchdog.log` | one line per watchdog run |
| `data/ops/alerts.log` | every alert, and whether its email was sent |
| `data/ops/backup.json` | the last backup result; `last_ok` is the last good backup |
| `data/ops/backup.log` | one line per backup run |

## Email
Alerts come from your own Gmail to itself. Two lines in `.env` (never committed):
```
QUANTPILOT_ALERT_EMAIL=you@gmail.com
QUANTPILOT_ALERT_APP_PASSWORD=abcd efgh ijkl mnop
```
The app password is created at myaccount.google.com → Security → 2-Step Verification → App passwords. It only allows sending mail, and you can revoke it there at any time. Without these lines, alerts go only to `data/ops/alerts.log`.

## Restore a backup
Stop the services first: `.venv/Scripts/python.exe scripts/ops/stop_data_supervisor.py` and `... --api`. Then, from `C:\Program Files\PostgreSQL\18\bin`:
```
dropdb   -h 127.0.0.1 -p 55432 -U <user> <database>
createdb -h 127.0.0.1 -p 55432 -U <user> <database>
pg_restore -h 127.0.0.1 -p 55432 -U <user> -d <database> --no-owner <file.dump>
```
The user and database come from `QUANTPILOT_MARKET_DATABASE_URL` in `.env`; set `PGPASSWORD` from it. Then start the services with `node scripts/start.cjs`.

Tested on 2026-10-23: a restore into a scratch database gave the same decision, setup and candle counts as the live database.

## Turn it off or on
- Remove the tasks: `powershell -File scripts/ops/install_ops_tasks.ps1 -Remove`
- Install or refresh them: `powershell -File scripts/ops/install_ops_tasks.ps1`
