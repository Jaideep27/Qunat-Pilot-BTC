"""Archived rule engine journal: paper-account states, triggers, setups and trades (market database, read-only now).

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


def _row(obj, *names):
    out = {n: getattr(obj, n) for n in names}
    for k, v in out.items():
        if hasattr(v, 'isoformat'):
            out[k] = pd.Timestamp(v).tz_convert('UTC').isoformat() if pd.Timestamp(v).tzinfo else                 pd.Timestamp(v).tz_localize('UTC').isoformat()
    return out


def hits(engine, limit=500):
    """The recorded rulebook setups, oldest first (read by the learner research)."""
    with Session(engine) as session:
        rows = session.scalars(select(RuleHit).order_by(RuleHit.time, RuleHit.playbook)).all()[-limit:]
        return [_row(r, 'id', 'time', 'playbook', 'side', 'status', 'payload', 'outcome', 'diagnosis', 'evaluated_at')
                for r in rows]


def accounts(engine):
    """Every archived paper account (trading was switched off on 2026-11-02, A15): id, status and final figures."""
    with Session(engine) as session:
        rows = session.scalars(select(RuleAccount).order_by(RuleAccount.id)).all()
    out = []
    for r in rows:
        state, f = r.state or {}, (r.state or {}).get('futures') or {}
        out.append(dict(id=r.id, status=state.get('status') or ('PAUSED' if f.get('paused') else 'STOPPED'),
                        retired_at=state.get('retired_at') or state.get('suspended_at'),
                        equity=f.get('equity'), trades=state.get('number', len(f.get('trades') or []))))
    return out
