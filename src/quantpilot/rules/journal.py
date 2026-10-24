"""Rule engine journal: experimental account state, triggers, hits and trades (market database).

Every row carries its full record as JSON, so the dashboard, MCP and later learning read the same data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import JSON, Column, DateTime, Float, String, select
from sqlalchemy.orm import Session

from quantpilot.storage.database import Base


def plain(value):
    """JSON-safe copy: numpy scalars become Python numbers, NaN and infinities become None."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


class RuleAccount(Base):
    __tablename__ = 'rule_accounts'
    id = Column(String(40), primary_key=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    state = Column(JSON, nullable=False)


class RuleTrigger(Base):
    __tablename__ = 'rule_triggers'
    id = Column(String(80), primary_key=True)
    time = Column(DateTime(timezone=True), nullable=False, index=True)
    type = Column(String(40), nullable=False)
    payload = Column(JSON, nullable=False)


class RuleHit(Base):
    __tablename__ = 'rule_hits'
    id = Column(String(80), primary_key=True)
    time = Column(DateTime(timezone=True), nullable=False, index=True)
    playbook = Column(String(16), nullable=False, index=True)
    side = Column(String(8), nullable=False)
    status = Column(String(32), nullable=False)
    payload = Column(JSON, nullable=False)
    outcome = Column(JSON)
    diagnosis = Column(String(32))
    evaluated_at = Column(DateTime(timezone=True))


class RuleTrade(Base):
    __tablename__ = 'rule_trades'
    id = Column(String(80), primary_key=True)
    playbook = Column(String(16), nullable=False, index=True)
    side = Column(String(8), nullable=False)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    exit_time = Column(DateTime(timezone=True), nullable=False, index=True)
    net = Column(Float, nullable=False)
    r = Column(Float)
    reason = Column(String(32), nullable=False)
    payload = Column(JSON, nullable=False)
    diagnosis = Column(String(32))
    diagnosed_at = Column(DateTime(timezone=True))


def _py(t):
    return pd.Timestamp(t).to_pydatetime()


def save_account(engine, account_id, state, now):
    with Session(engine) as session, session.begin():
        session.merge(RuleAccount(id=account_id, updated_at=_py(now), state=plain(state)))


def load_account(engine, account_id):
    with Session(engine) as session:
        row = session.get(RuleAccount, account_id)
        return row.state if row else None


def add_triggers(engine, rows):
    """Insert triggers not yet recorded; returns how many were new."""
    if not rows:
        return 0
    with Session(engine) as session, session.begin():
        known = set(session.scalars(select(RuleTrigger.id).where(RuleTrigger.id.in_([r['id'] for r in rows]))))
        new = [r for r in rows if r['id'] not in known]
        session.add_all(RuleTrigger(id=r['id'], time=_py(r['time']), type=r['type'], payload=plain(r)) for r in new)
    return len(new)


def add_hits(engine, hits):
    if not hits:
        return 0
    with Session(engine) as session, session.begin():
        known = set(session.scalars(select(RuleHit.id).where(RuleHit.id.in_([h['id'] for h in hits]))))
        new = [h for h in hits if h['id'] not in known]
        session.add_all(RuleHit(id=h['id'], time=_py(h['time']), playbook=h['playbook'], side=h['side'],
                                status=h['status'], payload=plain(h)) for h in new)
    return len(new)


def add_trade(engine, trade_id, trade):
    with Session(engine) as session, session.begin():
        if session.get(RuleTrade, trade_id):
            return False
        session.add(RuleTrade(id=trade_id, playbook=trade['plan'].get('playbook') or '?', side=
                              'long' if trade['side'] == 1 else 'short', entry_time=_py(trade['entry_time']),
                              exit_time=_py(trade['exit_time']), net=float(trade['net']), r=trade.get('r'),
                              reason=trade['reason'], payload=plain(trade)))
    return True


def _row(obj, *names):
    out = {n: getattr(obj, n) for n in names}
    for k, v in out.items():
        if hasattr(v, 'isoformat'):
            out[k] = pd.Timestamp(v).tz_convert('UTC').isoformat() if pd.Timestamp(v).tzinfo else \
                pd.Timestamp(v).tz_localize('UTC').isoformat()
    return out


def hits(engine, limit=500, pending_only=False):
    """Oldest first; `pending_only` returns every hit still waiting for its outcome."""
    with Session(engine) as session:
        query = select(RuleHit).order_by(RuleHit.time, RuleHit.playbook)
        if pending_only:
            query = query.where(RuleHit.diagnosis.is_(None))
        rows = session.scalars(query).all()
        rows = rows if pending_only else rows[-limit:]
        return [_row(r, 'id', 'time', 'playbook', 'side', 'status', 'payload', 'outcome', 'diagnosis', 'evaluated_at')
                for r in rows]


def trades(engine, limit=500, undiagnosed=False):
    with Session(engine) as session:
        query = select(RuleTrade).order_by(RuleTrade.exit_time)
        if undiagnosed:
            query = query.where(RuleTrade.diagnosis.is_(None))
        rows = session.scalars(query).all()[-limit:]
        return [_row(r, 'id', 'playbook', 'side', 'entry_time', 'exit_time', 'net', 'r', 'reason', 'payload', 'diagnosis',
                     'diagnosed_at') for r in rows]


def triggers(engine, limit=200):
    with Session(engine) as session:
        rows = session.scalars(select(RuleTrigger).order_by(RuleTrigger.time.desc()).limit(limit)).all()
        return [_row(r, 'id', 'time', 'type', 'payload') for r in rows]


def diagnose_hit(engine, hit_id, outcome, code, now):
    with Session(engine) as session, session.begin():
        row = session.get(RuleHit, hit_id)
        row.outcome, row.diagnosis, row.evaluated_at = plain(outcome), code, _py(now)


def diagnose_trade(engine, trade_id, code, detail, now):
    with Session(engine) as session, session.begin():
        row = session.get(RuleTrade, trade_id)
        row.diagnosis, row.diagnosed_at = code, _py(now)
        row.payload = plain(dict(row.payload, diagnosis=detail))
