"""Every outlook answer is stored and scored against what actually happened."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

import numpy as np
import pandas as pd
from sqlalchemy import JSON, Column, String, select
from sqlalchemy.orm import Session

from .store import Base, database, serialized

BUCKETS = ((1, '1h'), (4, '4h'), (12, '12h'), (24, '24h'), (72, '72h'), (168, '168h'))


class OutlookAnswer(Base):
    __tablename__ = 'outlook_answers'
    id = Column(String(32), primary_key=True)
    asked_at = Column(String(40), nullable=False, index=True)
    source = Column(String(16), nullable=False, index=True)
    thread_id = Column(String(32), index=True)
    payload = Column(JSON, nullable=False)


def bucket(h):
    return next(label for limit, label in BUCKETS if h <= limit)


def record(engine, result, source, thread_id=None, run_id=None, ai=None):
    answer_id = uuid.uuid4().hex
    with serialized(engine) as session:
        session.add(OutlookAnswer(id=answer_id, asked_at=result['asked_at'], source=source, thread_id=thread_id,
                                  payload={**result, 'run_id': run_id, 'ai': ai}))
    return answer_id


def set_ai_view(engine, answer_id, probability, reasons, run_id=None, now=None):
    """A view counts only before the outcome is knowable; without a run it may only fill an empty MCP answer."""
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    with serialized(engine) as session:
        row = session.get(OutlookAnswer, answer_id)
        if row is None or 'outcome' in row.payload:
            return False
        end = pd.Timestamp(row.payload['asked_at'])+pd.Timedelta(hours=row.payload['question']['horizon_hours'])
        if now >= end:
            return False
        if run_id is None:
            if row.source != 'mcp' or (row.payload.get('ai') or {}).get('probability') is not None:
                return False
        elif row.payload.get('run_id') != run_id:
            return False
        row.payload = {**row.payload, 'ai': dict(probability=float(probability), reasons=list(reasons)[:5], status='ok')}
    return True


def _outcome(payload, bars):
    q = payload['question']
    start = pd.Timestamp(payload['asked_at'])
    end = start+pd.Timedelta(hours=q['horizon_hours'])
    window = bars[(bars.index > start) & (bars.index <= end)]
    before = bars[bars.index <= start]
    if window.empty or bars.index.max() < end or before.empty:
        return None
    s, close = float(before.close.iloc[-1]), float(window.close.iloc[-1])
    if q['type'] == 'direction':
        hit = float(close > s)
    elif q['type'] == 'touch':
        side = q.get('side') or ('above' if q['level'] >= s else 'below')
        hit = float(window.high.max() >= q['level']) if side in ('above', 'up') else float(window.low.min() <= q['level'])
    elif q['type'] == 'big_move':
        threshold = q.get('threshold_pct', 5)/100
        up, down = window.high.max() >= s*(1+threshold), window.low.min() <= s*(1-threshold)
        hit = float({'up': up, 'down': down}.get(q.get('side'), up or down))
    elif q['type'] == 'range' and payload.get('range'):
        hit = float(payload['range']['low'] <= close <= payload['range']['high'])
    else:
        return dict(unscored=True, evaluated_at=end.isoformat())
    outcome = {'hit': hit, 'return': close/s-1, 'evaluated_at': end.isoformat()}
    head = payload.get('headline') or {}
    if head.get('probability') is not None:
        outcome['brier'] = (hit-head['probability'])**2
    if head.get('base_rate') is not None:
        outcome['base_brier'] = (hit-head['base_rate'])**2
    ai = payload.get('ai') or {}
    if ai.get('probability') is not None:
        outcome['ai_brier'] = (hit-ai['probability'])**2
    return outcome


def evaluate(engine, bars, now=None):
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    scored = 0
    with serialized(engine) as session:
        for row in session.scalars(select(OutlookAnswer)).all():
            payload = row.payload
            if 'outcome' in payload:
                continue
            end = pd.Timestamp(payload['asked_at'])+pd.Timedelta(hours=payload['question']['horizon_hours'])
            if end > now:
                continue
            outcome = _outcome(payload, bars)
            if outcome is not None:
                row.payload = {**payload, 'outcome': outcome}
                scored += 1
    return scored


def history(engine=None, limit=50):
    with Session(engine or database()) as session:
        rows = session.scalars(select(OutlookAnswer).order_by(OutlookAnswer.asked_at.desc()).limit(limit)).all()
        return [dict(id=r.id, source=r.source, thread_id=r.thread_id, **r.payload) for r in rows]


def scoreboard(engine=None):
    rows = [r for r in history(engine, 100000) if 'outcome' in r and 'hit' in r['outcome']]
    board = {}
    for key in sorted({f"{r['question']['type']}:{bucket(r['question']['horizon_hours'])}" for r in rows}):
        mine = sorted((r for r in rows if f"{r['question']['type']}:{bucket(r['question']['horizon_hours'])}" == key),
                      key=lambda r: r['asked_at'])
        last, independent = None, 0
        for r in mine:   # answers whose windows do not overlap
            t = pd.Timestamp(r['asked_at'])
            if last is None or t >= last+pd.Timedelta(hours=r['question']['horizon_hours']):
                independent, last = independent+1, t
        mean = lambda k: float(np.mean([r['outcome'][k] for r in mine if k in r['outcome']])) \
            if any(k in r['outcome'] for r in mine) else None
        quant, base, ai = mean('brier'), mean('base_brier'), mean('ai_brier')
        verdict = 'too early to judge'
        if independent >= 30 and quant is not None and base is not None:
            verdict = 'quant beats base rate' if quant < base else 'no edge over base rate'
            if ai is not None:
                verdict += '; AI beats quant' if ai < quant else '; AI does not beat quant'
        board[key] = dict(scored=len(mine), independent=independent, brier=quant, base_brier=base, ai_brier=ai,
                          hit_rate=float(np.mean([r['outcome']['hit'] for r in mine])), verdict=verdict)
    return board


def ai_calls_today(engine, now, sources):
    day = pd.Timestamp(now).strftime('%Y-%m-%d')
    return sum(1 for r in history(engine, 100000) if r['asked_at'][:10] == day and r['source'] in sources
               and (r.get('ai') or {}).get('status') in ('ok', 'failed', 'usage_limited'))


SCHEDULED = ['up or down next 1h', 'up or down next 4h', 'up or down next 24h', 'chance of a 3% drop in 24h',
             'chance of a 3% rally in 24h']


def scheduled_cycle(engine, ask_fn, now=None):
    from .config import settings
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    hour, day = now.strftime('%Y-%m-%dT%H'), now.strftime('%Y-%m-%d')
    mine = [r for r in history(engine, 2000) if r['source'] == 'scheduled']
    if any(r['asked_at'][:13] == hour for r in mine):
        return dict(asked=0, ai=0)
    ai_used = sum(1 for r in mine if r['asked_at'][:10] == day and (r.get('ai') or {}).get('status') != 'off')
    asked = ai = 0
    for text in SCHEDULED:
        use_ai = ai_used+ai < settings.ask_scheduled_ai_per_day
        ask_fn(text, ai=use_ai, source='scheduled')
        asked, ai = asked+1, ai+int(use_ai)
    return dict(asked=asked, ai=ai)
