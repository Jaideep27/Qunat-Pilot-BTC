"""Read services for the API and the MCP tools: market data, news, research, the outlook and chat, and the analysis
app. Every function returns the same envelope (data plus provenance). Nothing here trades."""
from __future__ import annotations
import json
import threading
from pathlib import Path
import numpy as np
import pandas as pd
from quantpilot.config import settings
from quantpilot.data.archives import utc
from quantpilot.storage.database import database
from quantpilot.ai import ask as ask_ai
from quantpilot.ai import news as news_cli  # alias: services.news() is the news envelope service

APP_VERSION = 'analysis-v1'   # A15: the analysis app (2026-11-02)


def clean(value):
    if isinstance(value, dict): return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)): return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, Path)): return str(value)
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating, float)): return float(value) if np.isfinite(value) else None
    if isinstance(value, np.bool_): return bool(value)
    return value

def envelope(data, observed=None, source="QuantPilot", max_age=600):
    now = pd.Timestamp.now(tz="UTC")
    age = max(0, (now-utc(observed)).total_seconds()) if observed else None
    return clean(dict(data=data, provenance=dict(source=source, observed_at=observed, served_at=now.isoformat(),
                     age_seconds=age, freshness="missing" if age is None else ("fresh" if age <= max_age else "stale")),
                      config_version=APP_VERSION, model_version=None))


def _market_engine():
    return database(settings.market_database_url) if settings.market_database_url else database()

def news(limit=20, compact=False):
    from quantpilot.ai.news import snapshot
    data = snapshot(database(), limit)
    observed = max((f['checked_at'] for f in data['feeds']), default=None)
    if compact:
        data = _compact_news(data)
    return envelope(data, observed, 'Publisher RSS excerpts / versioned Codex extraction', max_age=900)

def macro():
    from quantpilot.data.macro import snapshot
    data = snapshot(database())
    observed = data['recent'][0]['scheduled_at'] if data['recent'] else None
    # Schedules are known in advance; freshness concerns the latest observed release.
    return envelope(data, observed, 'FRED release calendar / Federal Reserve FOMC calendar / ALFRED first releases',
                    max_age=40*86400)

def shadow_forecasts(limit=50):
    """Latest research forecast per horizon, the forward record and how the champion was chosen."""
    from quantpilot.research.forecaster import champion_reports, forecasts, forward_summary
    rows = forecasts(database(), limit)
    latest = {}
    for row in rows:
        latest.setdefault(row['horizon'], row)
    found = champion_reports()
    direction, risk = found.get('direction', (None, {}))[1], found.get('risk', (None, {}))[1]
    data = dict(latest=list(latest.values()), recent=rows, forward=forward_summary(database()),
                champions=direction.get('champions', {}), backtest_overfitting=direction.get('backtest_overfitting', {}),
                range_champions=risk.get('range_champions', {}), range_overfitting=risk.get('range_overfitting', {}),
                drop_champion=risk.get('drop_champion'), volatility_champions=risk.get('volatility_champions', {}),
                status='shadow_only_never_trades',
                note='Research forecasts are recorded and scored forward; they never place or size paper trades.')
    observed = max((r['cutoff'] for r in latest.values()), default=None)
    return envelope(data, observed, 'Validated research champion, refitted on recent market history', max_age=1800)

def strategy_research():
    """Verdict of the latest pre-registered strategy research run and forward-only data collection."""
    from sqlalchemy import func, select
    from quantpilot.storage.market import FORWARD_VALUES, MarketObservation
    runs = sorted((settings.artifact_dir/'strategy-research').glob('*/report.json'), key=lambda p: p.stat().st_mtime)
    report = json.loads(runs[-1].read_text(encoding='utf-8')) if runs else None
    families = []
    for name, r in (report or {}).get('results', {}).items():
        rep, gate = r['report'], r['gate']
        families.append(dict(id=name, track=r['track'], kind=r['kind'], configurations=r['configurations'],
                             calls=rep['calls'], call_rate=rep.get('coverage'), hit_rate=rep.get('hit_rate'),
                             wilson_low=rep.get('wilson_low'), wilson_high=rep.get('wilson_high'),
                             break_even=rep.get('break_even'), mean_net=rep.get('mean_net'),
                             deflated_sharpe=gate['deflated_sharpe'], pbo=r.get('pbo'), passed=gate['passed'],
                             failed=[k for k, ok in gate['criteria'].items() if not ok]))
    forward = {}
    with database(settings.market_database_url).connect() as connection:
        for family in FORWARD_VALUES:
            rows, first, last = connection.execute(select(func.count(), func.min(MarketObservation.event_time),
                                                          func.max(MarketObservation.event_time))
                                                   .where(MarketObservation.family == family)).one()
            forward[family] = dict(rows=int(rows), first=first, last=last)
    folder = runs[-1].parent if runs else None
    agent_runs = sorted((settings.artifact_dir/'agent-research').glob('*/report.json'), key=lambda p: p.stat().st_mtime)
    agent = None
    if agent_runs:
        result = json.loads(agent_runs[-1].read_text(encoding='utf-8'))
        agent = dict(run=agent_runs[-1].parent.name, generated=result.get('generated'), trials=result.get('trials'),
                     families={name: dict(configurations=f['configurations'], pbo=f.get('pbo'), passed=f['gate']['passed'],
                                          failed=[k for k, ok in f['gate']['criteria'].items() if not ok], **f['report'])
                               for name, f in result.get('families', {}).items()},
                     configurations=[dict(id=name, **{k: c.get(k) for k in ('trades', 'hit_rate', 'break_even', 'mean_net',
                                                                             'account_return', 'max_drawdown_closed',
                                                                             'exit_reasons')})
                                     for name, c in result.get('configurations', {}).items()])
    data = dict(status='complete' if report else 'not_run', run=folder.name if folder else None, agent=agent,
                generated=(report or {}).get('generated'), trials=(report or {}).get('trials', {}),
                passing=(report or {}).get('passing', []), families=families,
                holdout_evaluated=bool(folder and (folder/'holdout.lock').exists()),
                forward=forward, next_test='2027-04-20',
                note='Pre-registered research; nothing here trades. See docs/research/strategy-research.md.')
    return envelope(data, (report or {}).get('generated'), 'QuantPilot pre-registered strategy research',
                    max_age=400*86400)

def candles(limit=288):
    from quantpilot.storage.market import candles as stored_candles
    rows = stored_candles(_market_engine(), limit=limit)
    return envelope(rows, rows[-1]['available_at'] if rows else None, 'PostgreSQL durable exchange candles')

def family(name, limit=1):
    """The latest observations of one external family (positioning, sentiment, funding, ...)."""
    from sqlalchemy import select
    from quantpilot.storage.market import MarketObservation, PERP
    with _market_engine().connect() as connection:
        observed = connection.execute(select(MarketObservation.__table__).where(
            MarketObservation.market == PERP, MarketObservation.family == name,
            MarketObservation.available_at <= pd.Timestamp.now(tz='UTC').to_pydatetime())
            .order_by(MarketObservation.event_time.desc()).limit(limit)).mappings().all()
    rows = [dict(event_time=utc(r['event_time']).isoformat(), available_at=utc(r['available_at']).isoformat(),
                 received_at=utc(r['received_at']).isoformat(), source=r['source'], **r['values']) for r in reversed(observed)]
    return envelope(rows, rows[-1]['available_at'] if rows else None, 'PostgreSQL durable '+name,
                    max_age={'sentiment': 172800, 'positioning': 900}.get(name, 600))

def coverage():
    path = settings.data_dir / "coverage.json"
    return envelope(json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else
                    dict(release_ready=False, status="not_audited", reason="Run quantpilot audit; historical coverage is mandatory"),
                    source="Checksummed historical coverage audit")

MAX_CURVE_POINTS = 400


def thin(points, limit=MAX_CURVE_POINTS):
    """Evenly spaced points for charting, always keeping the first and the last."""
    if len(points) <= limit:
        return points
    step = (len(points)-1)/(limit-1)
    return [points[round(i*step)] for i in range(limit)]


def _summarized(result):
    """Charts and agents need fold summaries and thinned curves, not every per-fold fill (about 5 MB)."""
    for row in result.get("comparisons", []):
        row["walk_forward_policy"] = [{k: v for k, v in fold.items() if not isinstance(v, (list, dict))}
                                      for fold in row.get("walk_forward_policy", [])]
        if row.get("trading", {}).get("curve"):
            row["trading"]["curve"] = thin(row["trading"]["curve"])
    for bench in result.get("benchmarks", {}).values():
        if isinstance(bench.get("fills"), list):
            bench["fills"] = len(bench["fills"])   # a count; the moving-average fills alone were 1.4 MB
        if bench.get("curve"):
            bench["curve"] = thin(bench["curve"])
    return result


def research(groups=None, detail=False):
    """The original (2026) release model's research: walk-forward comparisons and benchmarks."""
    p = settings.artifact_dir / "evaluation.json"
    result = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else dict(
        status="blocked", comparisons=[], benchmarks={}, reason="Audited historical data and a frozen experiment are required")
    if groups:
        result["comparisons"] = [r for r in result["comparisons"] if r["group"] in groups]
    return envelope(result if detail else _summarized(result), source="Purged walk-forward experiments and untouched holdout")

def health():
    return envelope(dict(status="ok", app=APP_VERSION))


def operations():
    from quantpilot.monitoring.operations import report
    result = report(_market_engine())
    return envelope(result, result['checked_at'], 'Readiness: database, candles and the analysis snapshot')


def _compact_news(data):
    """Small enough for an agent's tool output: excerpts trimmed, analyses flattened, raw runs dropped."""
    analyses = []
    for run in data['runs']:
        if run.get('status') != 'completed':
            continue
        for event in (run.get('result') or {}).get('events', []):
            analyses.append(dict(provider=run.get('provider'), model=run.get('model'), available_at=run.get('available_at'),
                                 summary=event['summary'], sentiment=event['sentiment'], relevance=event['relevance'],
                                 novelty=event.get('novelty'), entities=event.get('entities', []),
                                 source_ids=event['source_ids']))
    items = [dict(id=i['id'], title=i['title'], url=i['url'], source=i['source'], available_at=i['available_at'],
                  excerpt=(i.get('excerpt') or '')[:300]) for i in data['items']]
    return dict(items=items, analyses=analyses[:12], providers=data.get('providers'),
                analysis_status=data.get('analysis_status'), scope=data['scope'],
                forecasting_status=data['forecasting_status'])


ASK_SOURCES = ('ask', 'cli', 'scheduled')
_ai_lock = threading.Lock()


def _budget_file():
    return settings.data_dir/'ask-ai-budget.json'


def _parse_calls_today(day):
    try:
        state = json.loads(_budget_file().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 0
    return state.get('parse_calls', 0) if state.get('day') == day else 0


def _spend_parse(day):
    calls = _parse_calls_today(day)+1
    _budget_file().write_text(json.dumps(dict(day=day, parse_calls=calls)), encoding='utf-8')


def _context():
    from quantpilot.outlook.engine import load_context
    return load_context()


def _ai_allowed():
    """Ask-path Claude calls today (explanations recorded on answers plus parse attempts) under the daily limit."""
    from quantpilot.outlook.answers import ai_calls_today
    now = pd.Timestamp.now(tz='UTC')
    used = ai_calls_today(database(), now, ASK_SOURCES)+_parse_calls_today(now.strftime('%Y-%m-%d'))
    return used < settings.ask_daily_limit


def ask(text, ai=True, source='ask', ctx=None):
    from quantpilot.outlook.answers import record
    from quantpilot.outlook.engine import answer
    from quantpilot.outlook.questions import rule_parse
    ctx = ctx or _context()
    question = rule_parse(text, ctx.now.to_pydatetime(), ctx.price or 0)
    if question is None and ai and _ai_allowed() and _ai_lock.acquire(blocking=False):
        try:
            _spend_parse(pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d'))
            question = ask_ai.parse(text, ctx.now.to_pydatetime(), ctx.price or 0)
        except (news_cli.UsageLimitReached, RuntimeError, ValueError, FileNotFoundError, OSError):
            question = None
        finally:
            _ai_lock.release()
    if question is None:
        raise ValueError('Supported questions: up/down over 1h-7d, "will BTC touch $X by T", expected range, '
                         'chance of a big move, or "why is BTC moving".')
    result = answer(question, ctx)
    result['track_record']['forward'] = _forward(question)
    status, view = 'off', None
    if ai and not _ai_allowed():
        status = 'daily_limit'
    elif ai and not _ai_lock.acquire(blocking=False):
        status = 'busy'   # one Claude call at a time; the quant answer is returned now
    elif ai:
        try:
            view = ask_ai.explain(result)
            status = 'ok'
        except news_cli.UsageLimitReached:
            status = 'usage_limited'
        except TimeoutError:
            status = 'timeout'
        except (RuntimeError, ValueError, FileNotFoundError, OSError):
            status = 'failed'
        finally:
            _ai_lock.release()
    ai_record = dict(status=status, probability=(view or {}).get('ai_probability'), reasons=(view or {}).get('reasons', []),
                     answer=(view or {}).get('answer'))
    result['id'] = record(database(), result, source, ai=ai_record)
    result['ai'] = ai_record
    return result


def _forward(question):
    from quantpilot.outlook.answers import bucket, scoreboard
    return scoreboard(database()).get(f'{question.type}:{bucket(question.horizon_hours)}')


def outlook_question(question, source='mcp', thread_id=None, run_id=None, ctx=None):
    from quantpilot.outlook.answers import record
    from quantpilot.outlook.engine import answer
    from quantpilot.outlook.questions import Question
    question = Question.model_validate(question)
    result = answer(question, ctx or _context())
    result['track_record']['forward'] = _forward(question)
    result['id'] = record(database(), result, source, thread_id=thread_id, run_id=run_id)
    return result


def record_view(answer_id, probability, reasons, run_id=None):
    from quantpilot.outlook.answers import set_ai_view
    return dict(recorded=set_ai_view(database(), answer_id, probability, reasons, run_id=run_id))


def answers_view(limit=50):
    from quantpilot.outlook.answers import history, scoreboard
    data = dict(history=history(database(), limit), scoreboard=scoreboard(database()))
    observed = data['history'][0]['asked_at'] if data['history'] else None
    return envelope(data, observed, 'Outlook answers scored after their horizon', max_age=86400)


def chat_send(message, thread_id=None):
    from quantpilot.ai.chat import send
    return send(database(), message, thread_id)


def chat_threads():
    from quantpilot.ai.chat import threads
    return envelope(threads(database()), source='In-app chat')


def chat_thread(thread_id):
    from quantpilot.ai.chat import thread
    return envelope(thread(database(), thread_id), source='In-app chat')


# ---- archive: the paper accounts (trading was removed on 2026-11-02, A15) -------------------------------------------

def archive():
    from quantpilot.rules import journal
    rows = journal.accounts(_market_engine())
    return envelope(dict(accounts=rows, note='Paper trading was switched off on 2026-11-02 (A15). The accounts’ '
                         'records are kept; nothing trades.'), source='Archived paper accounts', max_age=10**9)


# ---- analysis app (A15) ---------------------------------------------------------------------------------------------

def analysis_snapshot():
    from quantpilot.analysis import snapshot
    snap = snapshot.latest(_market_engine())
    return envelope(snap, snap['built_at'] if snap else None, 'Analysis snapshot (completed candles; every 5 minutes)',
                    max_age=900)


def analysis_catalogue():
    from quantpilot.analysis import catalogue
    return envelope(catalogue.public(), source='Indicator library')


def analysis_briefs(limit=5):
    from quantpilot.analysis import ai
    rows = ai.briefs(_market_engine(), limit)
    return envelope(dict(briefs=rows, model=settings.analysis_brief_model, every_hours=settings.analysis_brief_hours),
                    rows[0]['created_at'] if rows else None, 'Claude market brief (subscription; AI analysis, not advice)',
                    max_age=settings.analysis_brief_hours*3600+1800)


def analysis_explanation(kind, ref, timeframe=None):
    from quantpilot.analysis import ai
    found = ai.cached(_market_engine(), kind, ref, timeframe)
    return envelope(found, found['created_at'] if found else None, 'Claude explanation (cached)', max_age=86400)


def analysis_explain(kind, ref, timeframe=None, studies=None):
    """One explanation (cached per snapshot). Raises LookupError when there is no snapshot or no such item."""
    from quantpilot.analysis import ai, snapshot
    engine = _market_engine()
    snap = snapshot.latest(engine)
    if snap is None:
        raise LookupError('No analysis snapshot yet; the analysis service builds one every 5 minutes.')
    try:
        result = ai.explain(engine, snap, kind, ref, timeframe, studies)
    except KeyError as exc:
        raise LookupError(f'Unknown {kind}: {exc}') from None
    return envelope(result, result['created_at'], 'Claude explanation (subscription; AI analysis, not advice)', max_age=86400)


def analysis_brief_now():
    from quantpilot.analysis import ai, snapshot
    engine = _market_engine()
    snap = snapshot.latest(engine)
    if snap is None:
        raise LookupError('No analysis snapshot yet; the analysis service builds one every 5 minutes.')
    last = ai.briefs(engine, 1)
    if last and pd.Timestamp.now(tz='UTC')-pd.Timestamp(last[0]['created_at']) < pd.Timedelta(minutes=15):
        raise ValueError('A brief was written less than 15 minutes ago.')
    record = ai.write_brief(engine, snap, reason='requested')
    return envelope(record, record['created_at'], 'Claude market brief (subscription; AI analysis, not advice)')


def analysis_levels():
    """Support, resistance, VWAP bands and the 4h likely range for the chart, from the latest snapshot."""
    from quantpilot.analysis import snapshot
    snap = snapshot.latest(_market_engine())
    overlay = (snap or {}).get('overlay')
    return envelope(dict(overlay=overlay, markers=[]), (overlay or {}).get('time'), 'Analysis snapshot levels', max_age=900)


def analysis_summary():
    """The compact snapshot Claude reads (no sparklines or curves): for the chat's tools."""
    from quantpilot.analysis import snapshot
    snap = snapshot.latest(_market_engine())
    return envelope(snapshot.compact(snap) if snap else None, snap['built_at'] if snap else None,
                    'Analysis snapshot (compact)', max_age=900)
