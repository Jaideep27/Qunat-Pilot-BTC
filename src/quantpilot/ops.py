"""Ops safety: the watchdog's checks and decisions, email alerts and nightly backups.

The watchdog runs every 5 minutes from Windows Task Scheduler. A problem counts only when it is seen on two
consecutive ticks; restart-worthy ones (API down, engine silent) restart the services at most 3 times in 2 hours.
Emails go out when a problem starts, every 6 hours while it lasts, and when it is resolved. Nothing here touches
trading, and no secret is ever printed or emailed.
"""
from __future__ import annotations

import shutil
import smtplib
import sqlite3
import subprocess
from email.message import EmailMessage
from pathlib import Path

import pandas as pd

ENGINE_STALE = pd.Timedelta(minutes=5)
DATA_STALE_S = 20*60
DISK_MIN_GB = 5.0
BACKUP_STALE = pd.Timedelta(hours=36)
CONFIRM_TICKS = 2
RESTARTS, WINDOW = 3, pd.Timedelta(hours=2)
REMIND = pd.Timedelta(hours=6)


def _minutes(delta):
    return int(delta.total_seconds()//60)


def check(p, now):
    """Problems in one look at the system. `p`: readiness, engine heartbeat, candle age, model health, disk, backup."""
    now, out = pd.Timestamp(now), []
    ready = p.get('ready')
    if not ready or not ready.get('ready'):
        out.append(dict(key='api', restart=True, text='The QuantPilot server is not responding.'))
    engine_at = p.get('engine_at')
    engine_ok = engine_at is not None and now-pd.Timestamp(engine_at) <= ENGINE_STALE
    if not engine_ok:
        since = 'has never reported' if engine_at is None else f'has not reported for {_minutes(now-pd.Timestamp(engine_at))} minutes'
        out.append(dict(key='engine', restart=True, text=f'The trading and data engine {since}.'))
    age = p.get('candle_age_s')
    if engine_ok and age is not None and age > DATA_STALE_S:
        out.append(dict(key='data', restart=False, text=f'Market data is {int(age//60)} minutes old (the exchange feed may be down).'))
    if p.get('model') == 'ALARM':
        out.append(dict(key='model', restart=False, text='Model health is on Alarm. Open the Rules page, Model health, for the reason.'))
    if p.get('disk_gb') is not None and p['disk_gb'] < DISK_MIN_GB:
        out.append(dict(key='disk', restart=False, text=f"Only {p['disk_gb']:.1f} GB of disk space is left."))
    backup_at = p.get('backup_at')
    if backup_at is not None and now-pd.Timestamp(backup_at) > BACKUP_STALE:
        out.append(dict(key='backup', restart=False,
                        text=f'The last successful backup was {int((now-pd.Timestamp(backup_at)).total_seconds()//3600)} hours ago.'))
    return out


def tick(state, problems, now):
    """One watchdog decision. Returns the new state and actions: ('restart',) and ('email', subject, body)."""
    now = pd.Timestamp(now)
    state = dict(active=dict(state.get('active', {})), streak=dict(state.get('streak', {})),
                 restarts=list(state.get('restarts', [])), gave_up_at=state.get('gave_up_at'))
    current = {p['key']: p for p in problems}
    state['streak'] = {k: state['streak'].get(k, 0)+1 for k in current}
    confirmed = {k: p for k, p in current.items() if state['streak'][k] >= CONFIRM_TICKS}
    actions, started, reminded, resolved, notes = [], [], [], [], []
    for k, p in confirmed.items():
        if k not in state['active']:
            state['active'][k] = dict(since=now.isoformat(), emailed=now.isoformat(), text=p['text'])
            started.append(p['text'])
        elif now-pd.Timestamp(state['active'][k]['emailed']) >= REMIND:
            state['active'][k].update(emailed=now.isoformat(), text=p['text'])
            reminded.append(p['text'])
    for k in [k for k in state['active'] if k not in current]:
        resolved.append(state['active'].pop(k)['text'])
    if any(p['restart'] for p in confirmed.values()):
        recent = [t for t in state['restarts'] if now-pd.Timestamp(t) < WINDOW]
        if len(recent) < RESTARTS:
            actions.append(('restart',))
            state['restarts'] = recent+[now.isoformat()]
            state['gave_up_at'] = None
            for k, p in confirmed.items():
                if p['restart']:
                    state['streak'][k] = 0
            notes.append(f'Restarting the services now (restart {len(recent)+1} of {RESTARTS} allowed in 2 hours).')
        elif state['gave_up_at'] is None:
            state['gave_up_at'] = now.isoformat()
            notes.append(f'The watchdog gave up restarting: {RESTARTS} restarts in 2 hours did not fix it. '
                         'It needs a person; it will try again once the 2-hour window frees up.')
    state['restarts'] = [t for t in state['restarts'] if now-pd.Timestamp(t) < WINDOW*2]
    if started or reminded or notes:
        lines = ([f'Problem: {t}' for t in started]+[f'Still happening: {t}' for t in reminded]+notes
                 +([f'Resolved: {t}' for t in resolved] if resolved else []))
        subject = 'QuantPilot: problem' if started or notes else 'QuantPilot: reminder'
        actions.append(('email', subject, _body(lines, now)))
    elif resolved:
        actions.append(('email', 'QuantPilot: resolved', _body([f'Resolved: {t}' for t in resolved], now)))
    return state, actions


def _body(lines, now):
    return ('\n'.join(lines)+f"\n\nChecked at {now.tz_convert('Asia/Kolkata'):%d %b %Y %H:%M} IST.\n"
            'Paper trading only; nothing here places real orders.\n')


def send_email(subject, body, cfg, smtp_factory=smtplib.SMTP_SSL):
    """Send through Gmail. Never raises: returns (ok, error) with any secret removed from the error."""
    email, password = cfg.get('email'), cfg.get('password')
    if hasattr(password, 'get_secret_value'):
        password = password.get_secret_value()
    if not email or not password:
        return False, 'email not configured'
    msg = EmailMessage()
    msg['Subject'], msg['From'], msg['To'] = subject, email, email
    msg.set_content(body)
    try:
        with smtp_factory('smtp.gmail.com', 465, timeout=20) as smtp:
            smtp.login(email, password)
            smtp.send_message(msg)
        return True, None
    except Exception as exc:
        text = f'{type(exc).__name__}: {exc}'
        for secret in {password, password.replace(' ', '')}:
            text = text.replace(secret, '***')
        return False, text[:300]
