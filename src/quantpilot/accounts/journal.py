"""A8 journal (market database): every account decision, trade and 30-trade review, one row each.

Ids are `<account>:<hit id>` so four accounts acting on the same setup never collide. Account state itself
lives in `rule_accounts` (see `journal.save_account`). `rule_trades` stays the reference account's table.
"""
from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, select
from sqlalchemy.orm import Session

from quantpilot.rules.journal import _py, _row, plain
from quantpilot.storage.database import Base


class AccountDecision(Base):
    __tablename__ = 'account_decisions'
    id = Column(String(120), primary_key=True)
    account = Column(String(16), nullable=False, index=True)
    hit_id = Column(String(80), nullable=False, index=True)
    time = Column(DateTime(timezone=True), nullable=False, index=True)
    decision = Column(String(32), nullable=False)
    score = Column(Float)
    confidence = Column(Float)
    cutoff = Column(Float)
    trade_id = Column(String(120))
    payload = Column(JSON, nullable=False)


class AccountTrade(Base):
    __tablename__ = 'account_trades'
    id = Column(String(120), primary_key=True)
    account = Column(String(16), nullable=False, index=True)
    round = Column(Integer, nullable=False)
    number = Column(Integer, nullable=False)
    playbook = Column(String(16), nullable=False)
    side = Column(String(8), nullable=False)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    exit_time = Column(DateTime(timezone=True), nullable=False, index=True)
    net = Column(Float, nullable=False)
    r = Column(Float)
    reason = Column(String(32), nullable=False)
    payload = Column(JSON, nullable=False)
    diagnosis = Column(String(32))
    diagnosed_at = Column(DateTime(timezone=True))
    explanation = Column(JSON)
    explained_at = Column(DateTime(timezone=True))


class AccountReview(Base):
    __tablename__ = 'account_reviews'
    id = Column(String(40), primary_key=True)
    account = Column(String(16), nullable=False, index=True)
    round = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(16), nullable=False)
    payload = Column(JSON, nullable=False)
    resumed_at = Column(DateTime(timezone=True))


DECISION_FIELDS = ('id', 'account', 'hit_id', 'time', 'decision', 'score', 'confidence', 'cutoff', 'trade_id', 'payload')
TRADE_FIELDS = ('id', 'account', 'round', 'number', 'playbook', 'side', 'entry_time', 'exit_time', 'net', 'r', 'reason',
                'payload', 'diagnosis', 'diagnosed_at', 'explanation', 'explained_at')


def add_decisions(engine, rows):
    """Insert decisions not yet recorded; returns how many were new."""
    if not rows:
        return 0
    rows = [dict(r, id=f"{r['account']}:{r['hit_id']}") for r in rows]
    with Session(engine) as session, session.begin():
        known = set(session.scalars(select(AccountDecision.id).where(AccountDecision.id.in_([r['id'] for r in rows]))))
        new = [r for r in rows if r['id'] not in known]
        session.add_all(AccountDecision(id=r['id'], account=r['account'], hit_id=r['hit_id'], time=_py(r['time']),
                                        decision=r['decision'], score=r.get('score'), confidence=r.get('confidence'),
                                        cutoff=r.get('cutoff'), trade_id=r.get('trade_id'), payload=plain(r.get('payload', {})))
                        for r in new)
    return len(new)


def trade_id(account, trade, index=None):
    return f"{account}:{trade['plan'].get('hit_id') or f'trade-{index}'}"


def add_trade(engine, account, round_, number, trade, index=None):
    tid = trade_id(account, trade, index)
    with Session(engine) as session, session.begin():
        if session.get(AccountTrade, tid):
            return False
        session.add(AccountTrade(id=tid, account=account, round=round_, number=number,
                                 playbook=trade['plan'].get('playbook') or '?', side='long' if trade['side'] == 1 else 'short',
                                 entry_time=_py(trade['entry_time']), exit_time=_py(trade['exit_time']),
                                 net=float(trade['net']), r=trade.get('r'), reason=trade['reason'], payload=plain(trade)))
    return True


def trades(engine, account=None, limit=500, undiagnosed=False, unexplained=False):
    with Session(engine) as session:
        query = select(AccountTrade).order_by(AccountTrade.exit_time)
        if account:
            query = query.where(AccountTrade.account == account)
        if undiagnosed:
            query = query.where(AccountTrade.diagnosis.is_(None))
        if unexplained:
            query = query.where(AccountTrade.diagnosis.is_not(None), AccountTrade.explanation.is_(None))
        return [_row(r, *TRADE_FIELDS) for r in session.scalars(query).all()[-limit:]]


def decisions(engine, account=None, limit=200):
    with Session(engine) as session:
        query = select(AccountDecision).order_by(AccountDecision.time.desc(), AccountDecision.id)
        if account:
            query = query.where(AccountDecision.account == account)
        return [_row(r, *DECISION_FIELDS) for r in session.scalars(query.limit(limit)).all()]


def diagnose(engine, trade_id_, code, detail, now):
    with Session(engine) as session, session.begin():
        row = session.get(AccountTrade, trade_id_)
        row.diagnosis, row.diagnosed_at = code, _py(now)
        row.payload = plain(dict(row.payload, diagnosis=detail))


def explain(engine, trade_id_, explanation, now):
    with Session(engine) as session, session.begin():
        row = session.get(AccountTrade, trade_id_)
        row.explanation, row.explained_at = plain(explanation), _py(now)


def save_review(engine, account, round_, payload, status, now):
    with Session(engine) as session, session.begin():
        session.merge(AccountReview(id=f'{account}:round-{round_}', account=account, round=round_, created_at=_py(now),
                                    status=status, payload=plain(payload)))


def reviews(engine, account=None):
    with Session(engine) as session:
        query = select(AccountReview).order_by(AccountReview.created_at.desc())
        if account:
            query = query.where(AccountReview.account == account)
        return [_row(r, 'id', 'account', 'round', 'created_at', 'status', 'payload', 'resumed_at')
                for r in session.scalars(query).all()]


def mark_resumed(engine, account, round_, now):
    with Session(engine) as session, session.begin():
        row = session.get(AccountReview, f'{account}:round-{round_}')
        if row is not None:
            row.status, row.resumed_at = 'resumed', _py(now)


def decision(engine, decision_id):
    with Session(engine) as session:
        row = session.get(AccountDecision, decision_id)
        return _row(row, *DECISION_FIELDS) if row else None
