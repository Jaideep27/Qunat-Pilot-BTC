"""Claude writes the analysis: automatic market briefs (Haiku) and on-demand explanations (Sonnet).

Both run `claude -p` on the owner's subscription through `ai.news.claude_json` (no tools, hooks, MCP or saved
session; API keys stripped). Claude only sees the compact snapshot, must use its numbers, and never tells the
reader to trade. Results are stored, so the dashboard and the public view read them without calling Claude again.
"""
from __future__ import annotations

import json
import threading
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, Column, DateTime, String, func, select
from sqlalchemy.orm import Session

from quantpilot.analysis import catalogue, snapshot
from quantpilot.config import settings
from quantpilot.storage.database import Base

RULES = ('Use only facts and numbers present in the JSON you are given; if something is missing, say so. '
         'Write plain English for a non-technical reader and explain any term you use. '
         'Never tell the reader to buy, sell, enter, exit or size a trade, and give no price targets beyond the '
         'levels and likely range in the JSON. Say clearly when signals conflict or are weak. '
         'None of the strategies has been proven to make money; do not imply otherwise. Times are UTC.')
BRIEF_SYSTEM = ('You are a senior market analyst writing a short Bitcoin market brief for the owner of a research '
                'dashboard. '+RULES)
EXPLAIN_SYSTEM = ('You are a patient senior market analyst explaining one item on a Bitcoin research dashboard. '
                  'Write 3 to 6 sentences: what it shows right now, why that matters, and what would change the '
                  'picture. '+RULES)
_lock = threading.Lock()


class Brief(BaseModel):
    model_config = ConfigDict(extra='forbid')
    headline: str = Field(max_length=160)
    overall: Literal['leaning up', 'leaning down', 'mixed', 'quiet']
    summary: str = Field(max_length=900)
    trend: str = Field(max_length=500)
    momentum: str = Field(max_length=500)
    volatility: str = Field(max_length=500)
    volume_flow: str = Field(max_length=500)
    positioning: str = Field(max_length=500)
    strategies: str = Field(max_length=700)
    news_calendar: str = Field(max_length=700)
    levels_to_watch: list[str] = Field(max_length=5)
    risks: list[str] = Field(max_length=4)


class Explanation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(max_length=120)
    explanation: str = Field(max_length=1500)
    what_to_watch: list[str] = Field(max_length=3)


class AnalysisBrief(Base):
    __tablename__ = 'analysis_briefs'
    id = Column(String(60), primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    payload = Column(JSON, nullable=False)


class AnalysisExplanation(Base):
    __tablename__ = 'analysis_explanations'
    id = Column(String(200), primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    kind = Column(String(20), nullable=False)
    ref = Column(String(60), nullable=False, index=True)
    payload = Column(JSON, nullable=False)


class RateLimited(Exception):
    pass


def _claude(system, prompt, schema, model):
    from quantpilot.ai.news import claude_json
    return claude_json(system, prompt, schema, model=model, effort=settings.analysis_effort)


# ---- briefs ---------------------------------------------------------------------------------------------------------

def briefs(engine, limit=5):
    with Session(engine) as session:
        rows = session.scalars(select(AnalysisBrief).order_by(AnalysisBrief.created_at.desc()).limit(limit)).all()
        return [r.payload for r in rows]


def write_brief(engine, snap, now=None, reason='scheduled', call=None):
    """One brief from `snap`. A failure is stored too (status and error), so the schedule does not retry it."""
    now = pd.Timestamp(now or pd.Timestamp.now(tz='UTC'))
    model = settings.analysis_brief_model
    record = dict(id=f'brief:{now.isoformat()}', created_at=now.isoformat(), snapshot_time=snap['built_at'],
                  price=snap['price']['last'], model=model, reason=reason)
    try:
        raw = (call or _claude)(BRIEF_SYSTEM, json.dumps(snapshot.compact(snap), default=str), Brief.model_json_schema(), model)
        record |= dict(status='ok', brief=Brief.model_validate(raw).model_dump())
    except Exception as exc:
        record |= dict(status='limit' if type(exc).__name__ == 'UsageLimitReached' else 'failed',
                       error=f'{type(exc).__name__}: {exc}'[:300])
    with Session(engine) as session, session.begin():
        session.merge(AnalysisBrief(id=record['id'], created_at=now.to_pydatetime(), payload=record))
    return record


def due(engine, snap, now=None):
    """'scheduled' at each new block of `analysis_brief_hours`, 'macro' after a fresh CPI/jobs/FOMC release, else None."""
    now = pd.Timestamp(now or pd.Timestamp.now(tz='UTC'))
    last = briefs(engine, 1)
    last_at = pd.Timestamp(last[0]['created_at']) if last else None
    block = now.floor(f'{settings.analysis_brief_hours}h')
    if last_at is None or last_at < block:
        return 'scheduled'
    for event in snap.get('calendar', {}).get('recent', []):
        at = pd.Timestamp(event['at'])
        if now-pd.Timedelta(hours=1) <= at <= now and last_at < at:
            return 'macro'
    return None


# ---- explanations ---------------------------------------------------------------------------------------------------

def _recent_news(limit=60):
    try:
        from quantpilot.ai.news import snapshot as news_snapshot
        from quantpilot.api.services import _compact_news
        from quantpilot.storage.database import database
        data = _compact_news(news_snapshot(database(), limit))
        return data['items'], data['analyses']
    except Exception:
        return [], []


def _context(snap, kind, ref, timeframe=None, studies=None):
    """The slice of the snapshot Claude needs for one explanation."""
    base = dict(price=snap['price'], market_type=snap['regime']['text'], snapshot_time=snap['built_at'])
    if kind == 'indicator':
        meta = catalogue.BY_ID.get(ref)
        if meta is None:
            raise KeyError(ref)
        tfs = {tf: next((i for i in v['items'] if i['id'] == ref), None) for tf, v in snap['timeframes'].items()}
        live = next((i for i in snap['market']['items'] if i['id'] == ref), None)
        pick = lambda r: None if r is None else dict(value=r['value'], reading=r['label'], meaning=r['sentence'],
                                                     recent=[v for v in r['spark'][-12:]])
        return base | dict(indicator=dict(name=meta['name'], settings=meta['settings'], what=meta['what'], how=meta['how'],
                                          zones=[z[1] for z in meta['zones']]),
                           focus_timeframe=timeframe, readings={tf: pick(r) for tf, r in tfs.items()} if meta['scope'] == 'timeframe'
                           else dict(live=pick(live)),
                           group_summary=snap['summary'].get(timeframe or '1h', {}).get(meta['group']))
    if kind == 'strategy':
        view = next((s for s in snap['strategies']['styles'] if s['id'] == ref), None)
        if view is None:
            view = next((p for p in snap['strategies']['rulebook']['playbooks'] if p['id'] == ref), None)
        if view is None:
            raise KeyError(ref)
        view = {k: v for k, v in view.items() if k != 'history'} | dict(
            history={k: v for k, v in (view.get('history') or {}).items() if k != 'curve'} or None)
        return base | dict(strategy=view, levels=snap['levels'][:8])
    if kind == 'news':
        items, analyses = snap['headlines']['items'], snap['headlines']['analyses']
        item = next((h for h in items if h['id'] == ref), None)
        if item is None:              # the News page lists more items than the snapshot keeps
            items, analyses = _recent_news()
            item = next((h for h in items if h['id'] == ref), None)
        if item is None:
            raise KeyError(ref)
        notes = [a for a in analyses if ref in (a.get('source_ids') or [])]
        return base | dict(headline=item, ai_notes=notes, calendar=snap['calendar'])
    if kind == 'chart':
        tf = timeframe or '1h'
        wanted = set(studies or [])
        items = [i for i in snap['timeframes'][tf]['items'] if set(catalogue.BY_ID[i['id']].get('study') or []) & wanted] \
            or snap['timeframes'][tf]['items']
        return base | dict(timeframe=tf, studies=sorted(wanted),
                           readings=[dict(name=catalogue.BY_ID[i['id']]['name'], value=i['value'], reading=i['label'])
                                     for i in items], levels=snap['levels'], group_summary=snap['summary'].get(tf))
    raise KeyError(kind)


def cache_key(kind, ref, timeframe, snap):
    stamp = 'news' if kind == 'news' else snap['built_at']
    return f'{kind}:{ref}:{timeframe or "-"}:{stamp}'


def cached(engine, kind, ref, timeframe=None):
    """The latest stored explanation for this item (any snapshot), for the public view."""
    with Session(engine) as session:
        q = select(AnalysisExplanation).where(AnalysisExplanation.kind == kind, AnalysisExplanation.ref == ref)
        rows = session.scalars(q.order_by(AnalysisExplanation.created_at.desc()).limit(20)).all()
        for r in rows:
            if timeframe is None or r.payload.get('timeframe') in (None, timeframe):
                return r.payload
    return None


def explain(engine, snap, kind, ref, timeframe=None, studies=None, now=None, call=None):
    """An explanation, from the cache when this snapshot already has one; otherwise one Sonnet call."""
    now = pd.Timestamp(now or pd.Timestamp.now(tz='UTC'))
    key = cache_key(kind, ref, timeframe if kind in ('indicator', 'chart') else None, snap)
    if kind == 'chart':
        key += ':'+','.join(sorted(studies or []))
    context = _context(snap, kind, ref, timeframe, studies)
    with _lock:
        with Session(engine) as session:
            hit = session.get(AnalysisExplanation, key[:200])
            if hit is not None:
                return hit.payload | dict(cached=True)
            recent = session.scalar(select(func.count()).select_from(AnalysisExplanation)
                                    .where(AnalysisExplanation.created_at > (now-pd.Timedelta(hours=1)).to_pydatetime()))
        if recent >= settings.analysis_explain_per_hour:
            raise RateLimited(f'{settings.analysis_explain_per_hour} explanations an hour is the limit; try again later.')
        model = settings.analysis_explain_model
        raw = (call or _claude)(EXPLAIN_SYSTEM, json.dumps(context, default=str), Explanation.model_json_schema(), model)
        record = dict(id=key[:200], kind=kind, ref=ref, timeframe=timeframe, studies=sorted(studies or []),
                      created_at=now.isoformat(), snapshot_time=snap['built_at'], model=model,
                      **Explanation.model_validate(raw).model_dump())
        with Session(engine) as session, session.begin():
            session.merge(AnalysisExplanation(id=record['id'], created_at=now.to_pydatetime(), kind=kind, ref=ref[:60],
                                              payload=record))
    return record | dict(cached=False)
