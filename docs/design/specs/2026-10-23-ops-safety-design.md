# Ops safety: watchdog, email alerts, nightly backups — design

Date: 2026-10-23. Branch `rule-engine`. Approved by the owner in chat ("yes build them"). The owner chose **email (Gmail)** alerts and backups to the **laptop and OneDrive**.

## Why
- On 2026-10-23 the services stopped for 10 minutes and nothing restarted them.
- The owner learns about problems only when they open the dashboard.
- All journals (decisions, trades, reviews) and the forward-only data (liquidations, order book, news) exist on one laptop disk.

## Goals
1. **Watchdog.** Every 5 minutes, check the system. Restart the services when the API or the supervisor is down or stuck.
2. **Email alerts.** Send an email when a problem starts, a reminder every 6 hours while it lasts, and a "resolved" email when it ends.
3. **Nightly backups.** Back up PostgreSQL and SQLite. Keep 7 on the laptop and copy the latest 2 to OneDrive. Verify that each backup is readable.

## Non-goals and anti-goals
- No cloud services, no paid tools and no new always-running process. Windows Task Scheduler runs short scripts.
- The watchdog must never restart in a loop. At most 3 restarts per 2 hours; past that, alert and stop restarting.
- It must never touch VS Code, Chrome or anything outside QuantPilot's own services.
- Never print or email secrets: database URLs and the app password.
- Nothing here changes trading.

## Decisions
**Checks** (`ops.check`):

| Check | Problem when | Action |
|---|---|---|
| API | `GET /api/v1/ready` fails or is not ready | restart |
| Supervisor | feed `rules:engine` is older than 5 minutes | restart |
| Data | completed candles are older than 20 minutes while the supervisor is alive | alert only |
| Model health | feed `rules:model-health` is ALARM | alert only |
| Disk | under 5 GB free | alert only |
| Backup | the last successful backup is older than 36 hours | alert only |

**Restart rule.** A restart-worthy problem must be seen on **2 consecutive ticks** (≥ 5 minutes apart), so a normal restart or a slow minute does not trigger one. The restart runs `node scripts/start.cjs --restart-services` with stdout/stderr redirected to `.verification/services/watchdog-restart-*.log`. Unredirected launches died on 2026-10-23. The budget is 3 restarts in 2 hours.

**State.** `data/ops/state.json` holds the active problems with first-seen and last-emailed times, the consecutive-tick counters, the restart times and the last backup result.

**Email.** Gmail SMTP over SSL (smtp.gmail.com:465) with an app password. Settings `QUANTPILOT_ALERT_EMAIL` and `QUANTPILOT_ALERT_APP_PASSWORD` live in `.env` (git-ignored). If they are not set, alerts are written to `data/ops/alerts.log` only. Subjects start with `QuantPilot:`. Bodies are plain English and contain no secrets.

**Backups** (`ops.backup`):
- `pg_dump -Fc` of the market database goes to `data/backups/quantpilot-pg-YYYYMMDD-HHMM.dump`.
- A SQLite online backup goes to `data/backups/quantpilot-sqlite-…db`.
- Each dump is verified with `pg_restore --list`, which must exit 0 and list the account tables.
- 7 of each are kept locally. The latest 2 are copied to `%USERPROFILE%\OneDrive\QuantPilot-backups`, and the copy's size is checked.
- The result is recorded in the state file.
- One full restore test runs into a scratch database at build time. `docs/operations/operations.md` describes how to restore.

**Scheduling.** Two Task Scheduler tasks run as the owner, only when they are logged on, through `pythonw.exe` (no console window):
- "QuantPilot Watchdog": every 5 minutes, plus at logon;
- "QuantPilot Backup": daily at 02:30 local time, and run late if the laptop was asleep.

Both are created by `scripts/install_ops_tasks.ps1` and removed by its `-Remove` switch.

## Left to the builder
The module layout and the exact email wording.
