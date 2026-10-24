# Ops Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A watchdog that restarts stuck services, email alerts, and nightly verified backups to the laptop and OneDrive.

**Architecture:**
- `src/quantpilot/ops.py` holds the pure check/decision logic with injected probes, plus email and backup helpers.
- `scripts/watchdog.py` and `scripts/backup.py` are thin runners.
- `scripts/install_ops_tasks.ps1` registers the Task Scheduler entries.

**Tech Stack:** Python stdlib (smtplib, sqlite3, subprocess, shutil), httpx, SQLAlchemy, the PostgreSQL 18 client tools, PowerShell `Register-ScheduledTask`.

**Spec:** `docs/design/specs/2026-10-23-ops-safety-design.md`

## Global Constraints
- Never print, log or email secrets (database URLs, app password).
- At most 3 restarts in 2 hours; a restart needs a problem seen on 2 consecutive ticks.
- Restarts always redirect stdout/stderr to files.
- Never touch processes other than QuantPilot's own (only `start.cjs --restart-services`).
- Tests: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_ops.py`.

## Review Focus
1. A transient failure on one tick must not restart anything.
2. A restart loop must stop at the budget and email once.
3. Email failure, such as a wrong password or no internet, must not crash the watchdog. It is logged instead.
4. A failed backup (pg_dump error, OneDrive missing) must be reported, and must not delete older good backups.
5. Missing `.env` alert settings mean alerts go to the log file only.

---

### Task 1: checks, decisions, alerts

**Files:** Create `src/quantpilot/ops.py`, `tests/test_ops.py`. Modify `src/quantpilot/config.py` (add `alert_email`, `alert_app_password: SecretStr | None`).

**Produces:**
- `Problem = dict(key, text, restart: bool)`
- `check(probes: dict, now) -> list[Problem]`. The probes are the readiness result, `rules:engine` updated_at, the candle age, the model-health overall, free disk GB and the last backup time.
- `tick(state: dict, problems, now) -> (state, actions)`. Actions are a list of `('restart',)`, `('email', subject, body)` and `('log', text)`.
  - A restart happens when a restart problem has been seen on ≥ 2 consecutive ticks and fewer than 3 restarts happened in the last 2 hours. A restart clears the counters.
  - Once the budget is exhausted, send one "restart budget used up" email.
  - Email when a new problem appears, a reminder after 6 hours, and "resolved" when a problem disappears.
- `send_email(subject, body, cfg, smtp_factory=smtplib.SMTP_SSL) -> bool`. It never raises and returns False on failure.

- [ ] **Step 1: Failing tests:**
  - `test_one_bad_tick_does_not_restart_two_do`
  - `test_restart_budget_stops_at_three_in_two_hours_and_emails_once`
  - `test_new_problem_emails_reminds_after_six_hours_and_resolves`
  - `test_check_flags_each_condition` (api down, stale supervisor, stale data with a live supervisor, model ALARM, disk, backup age)
  - `test_send_email_never_raises_and_hides_the_password` (fake SMTP raising; the password is not in the logged text)
  - `test_no_settings_means_log_only`
- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS), commit.

### Task 2: backups

**Files:** Modify `ops.py`, `tests/test_ops.py`. Create `scripts/backup.py`.

**Produces:**
- `backup(now, pg_bin, db_url, sqlite_path, local_dir, offsite_dir, keep_local=7, keep_offsite=2, run=subprocess.run) -> dict(ok, files, size_mb, offsite, error)`
- `prune(dir, pattern, keep)`

- [ ] **Step 1: Failing tests:**
  - `test_backup_dumps_verifies_copies_and_prunes`: a fake `run` writes a dump file and returns 0 for pg_restore listing the account tables.
  - `test_failed_dump_keeps_old_backups_and_reports`
  - `test_missing_offsite_folder_is_reported_not_fatal`
- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS). Then a real backup and a real restore test into a scratch database (`quantpilot_restore_test`, dropped afterwards). Commit.

### Task 3: runners and scheduling

**Files:** Create `scripts/watchdog.py`, `scripts/install_ops_tasks.ps1`.

- [ ] `watchdog.py` gathers the probes:
  - httpx GET `/api/v1/ready` with a 10 s timeout;
  - the feed statuses from the market DB;
  - `shutil.disk_usage`;
  - the backup time from the state.

  It then calls `tick` and executes the actions. A restart runs `subprocess.Popen` of node with stdout/stderr to log files, detached. Each run appends one line to `data/ops/watchdog.log`.
- [ ] `install_ops_tasks.ps1` registers both tasks with `pythonw.exe`: every 5 minutes plus at logon, and daily at 02:30 with StartWhenAvailable. `-Remove` unregisters them.
- [ ] Verify: run `watchdog.py` once by hand (healthy → no action); install the tasks; check `Get-ScheduledTask` and the next run time. Commit.

### Task 4: docs and email setup
- [ ] Write `docs/operations/operations.md` (what runs, where the logs and backups are, how to restore, how to remove the tasks) and add a line to `progress.md`.
- [ ] Guide the owner through creating a Gmail app password and putting it in `.env`; send a test email.
- [ ] Commit.
