"""Shared read services. API and MCP return these exact envelopes."""
from __future__ import annotations
import json
from pathlib import Path
from threading import Lock
import joblib
import numpy as np
import pandas as pd
from .config import settings, RiskConfig
from .data import load_frames, utc
from .features import build_features
from .engine import gate
from .store import database, portfolio
from . import ask_ai, news as news_cli  # alias: services.news() is the news envelope service
import threading

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
                      config_version=RiskConfig().identity, model_version=model_version()))

def remote(name):
    from .snapshots import read
    cached = read(name)
    if not cached: return None
    cached = dict(cached)
    p = cached["provenance"]
    now = pd.Timestamp.now(tz="UTC")
    p["served_at"] = now.isoformat()
    if p.get("observed_at"):
        p["age_seconds"] = max(0, (now-utc(p["observed_at"])).total_seconds())
        limit = {"sentiment":172800,"positioning":900}.get(name,600)
        p["freshness"] = "fresh" if p["age_seconds"] <= limit else "stale"
    return cached

def model_version():
    p = settings.artifact_dir / "evaluation.json"
    return json.loads(p.read_text(encoding="utf-8-sig")).get("metadata",{}).get("version") if p.exists() else None


def news(limit=20, compact=False):
    from .news import snapshot
    data = snapshot(database(), limit)
    observed = max((f['checked_at'] for f in data['feeds']), default=None)
    if compact:
        data = _compact_news(data)
    return envelope(data, observed, 'Publisher RSS excerpts / versioned Codex extraction', max_age=900)

def macro():
    from .macro import snapshot
    data = snapshot(database())
    observed = data['recent'][0]['scheduled_at'] if data['recent'] else None
    # Schedules are known in advance; freshness concerns the latest observed release.
    return envelope(data, observed, 'FRED release calendar / Federal Reserve FOMC calendar / ALFRED first releases',
                    max_age=40*86400)

def shadow_forecasts(limit=50):
    """Latest research forecast per horizon, the forward record and how the champion was chosen."""
    from .forecaster import champion_reports, forecasts, forward_summary
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
    from .market_store import FORWARD_VALUES, MarketObservation
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
    data = dict(status='complete' if report else 'not_run', run=folder.name if folder else None,
                generated=(report or {}).get('generated'), trials=(report or {}).get('trials', {}),
                passing=(report or {}).get('passing', []), families=families,
                holdout_evaluated=bool(folder and (folder/'holdout.lock').exists()),
                forward=forward, next_test='2027-04-20',
                note='Pre-registered research; nothing here trades. See docs/strategy-research.md.')
    return envelope(data, (report or {}).get('generated'), 'QuantPilot pre-registered strategy research',
                    max_age=400*86400)

def market_root():
    live = settings.data_dir / "live"
    return live if (live / "candles.parquet").exists() else settings.data_dir

def candles(limit=288):
    if settings.market_database_url:
        from .market_store import candles as stored_candles
        rows=stored_candles(database(settings.market_database_url),limit=limit)
        if rows:
            return envelope(rows,rows[-1]['available_at'],'PostgreSQL durable exchange candles')
    if not (market_root() / "candles.parquet").exists():
        cached = remote("candles")
        if cached:
            cached["data"] = cached["data"][-limit:]
            return cached
    candle_path = market_root()/"candles.parquet"
    d = pd.read_parquet(candle_path) if candle_path.exists() else pd.DataFrame()
    if len(d): d = d.assign(ema21=d.close.ewm(span=21, adjust=False, min_periods=21).mean())
    rows = d.tail(limit).to_dict("records") if len(d) else []
    return envelope(rows, d.available_at.max().isoformat() if len(d) else None, "Binance USD-M perpetual archives / REST")

def family(name, limit=1):
    if settings.market_database_url:
        from sqlalchemy import select
        from .market_store import MarketObservation,PERP
        with database(settings.market_database_url).connect() as connection:
            observed=connection.execute(select(MarketObservation.__table__).where(MarketObservation.market==PERP,
                MarketObservation.family==name,MarketObservation.available_at<=pd.Timestamp.now(tz='UTC').to_pydatetime())
                .order_by(MarketObservation.event_time.desc()).limit(limit)).mappings().all()
        if observed:
            rows=[dict(event_time=utc(r['event_time']).isoformat(),available_at=utc(r['available_at']).isoformat(),
                       received_at=utc(r['received_at']).isoformat(),source=r['source'],**r['values']) for r in reversed(observed)]
            return envelope(rows,rows[-1]['available_at'],'PostgreSQL durable '+name,
                            max_age={'sentiment':172800,'positioning':900}.get(name,600))
    if not (market_root() / f"{name}.parquet").exists():
        cached = remote(name)
        if cached: return cached
    family_path = market_root()/f"{name}.parquet"
    d = pd.read_parquet(family_path) if family_path.exists() else pd.DataFrame()
    if len(d): d = d[d.available_at <= pd.Timestamp.now(tz="UTC")]
    return envelope(d.tail(limit).to_dict("records") if len(d) else [], d.available_at.max().isoformat() if len(d) else None,
                    "Alternative.me (24h conservative lag)" if name == "sentiment" else "Binance public market data",
                    max_age={"sentiment":172800,"positioning":900}.get(name,600))

# API and MCP share one immutable-by-convention feature snapshot per file generation.
_feature_lock = Lock()
_feature_cache = {"key":None, "value":None}


def feature_data():
    root = market_root()
    files = sorted(root.glob("*.parquet"))
    key = tuple((str(p.resolve()),p.stat().st_mtime_ns,p.stat().st_size) for p in files)
    with _feature_lock:
        if key == _feature_cache["key"]:
            return _feature_cache["value"]
        frames = load_frames(root)
        value = build_features(frames["candles"], frames) if "candles" in frames and not frames["candles"].empty else None
        _feature_cache.update(key=key, value=value)
        return value

def indicators():
    built = feature_data()
    if built is None: return remote("indicators") or envelope({})
    f, _, d = built
    return envelope(f.iloc[-1].to_dict(), f.index[-1].isoformat(), "Causal indicators calculated from completed Binance bars")

def coverage():
    path = settings.data_dir / "coverage.json"
    if not path.exists():
        cached = remote("coverage")
        if cached: return cached
    return envelope(json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else
                    dict(release_ready=False, status="not_audited", reason="Run quantpilot audit; historical coverage is mandatory"),
                    source="Checksummed historical coverage audit")

def prediction():
    built = feature_data()
    if built is None:
        cached = remote("predictions")
        if cached:
            if cached["provenance"]["freshness"] != "fresh":
                cached["data"] = {**cached["data"], "action":"NO_TRADE", "reasons":["STALE_DATA"]}
            return cached
    model_path = settings.artifact_dir / "released.joblib"
    report = coverage()["data"]
    missing = dict(action="NO_TRADE", reasons=["MODEL_RELEASE_BLOCKED"], probability=None, explanation=None)
    if built is None or not model_path.exists() or not report.get("release_ready"):
        return envelope(missing)
    # Artifacts are local trusted outputs. Never load untrusted joblib files from requests.
    bundle = joblib.load(model_path)
    f, _, d = built
    last = f.tail(1)
    if set(bundle["columns"])-set(last.columns):
        return envelope({**missing, "reasons":["MODEL_SCHEMA_MISMATCH"]}, f.index[-1].isoformat())
    if last[bundle["columns"]].isna().any().any():
        return envelope({**missing, "reasons":["MISSING_CRITICAL_INPUT"]}, f.index[-1].isoformat())
    ingestion_status = market_root() / "ingestion-status.json"
    if ingestion_status.exists() and json.loads(ingestion_status.read_text(encoding="utf-8")).get("errors"):
        return envelope({**missing, "reasons":["MISSING_CRITICAL_INPUT"]}, f.index[-1].isoformat())
    probability = float(bundle["model"].predict_proba(last[bundle["columns"]])[0,1])
    t = f.index[-1]
    forecast = dict(probability=probability, up_magnitude=bundle["up"], down_magnitude=bundle["down"],
                    atr=float(last.atr.iloc[0]), observed_at=t.isoformat(), model_version=bundle["metadata"]["version"],
                    funding_rate=float(d.funding_rate.iloc[-1]), funding_observable=True, complete=True, threshold=bundle["threshold"])
    forecast["input_available_at"] = {c:str(d[c].iloc[-1]) for c in d if c.endswith("_available_at")}
    state = paper()["data"]
    c = RiskConfig(threshold=bundle["threshold"])
    decision = gate(state, forecast, float(d.close.iloc[-1]), pd.Timestamp.now(tz="UTC"), c)
    from .research import explanations
    decision.update(forecast=forecast, regime="trending" if last.regime.iloc[0] else "ranging",
                    explanation=explanations(bundle["model"], last[bundle["columns"]], bundle["kind"]))
    return envelope(decision, t.isoformat(), "Frozen calibrated model, completed bars")

def research(groups=None):
    p = settings.artifact_dir / "evaluation.json"
    if not p.exists():
        cached = remote("backtests")
        if cached:
            if groups: cached["data"]["comparisons"] = [r for r in cached["data"]["comparisons"] if r["group"] in groups]
            return cached
    result = json.loads(p.read_text(encoding="utf-8-sig")) if p.exists() else dict(
        status="blocked", comparisons=[], benchmarks={}, reason="Audited historical data and a frozen experiment are required")
    if groups:
        result["comparisons"] = [r for r in result["comparisons"] if r["group"] in groups]
    return envelope(result, source="Purged walk-forward experiments and untouched holdout")

def paper():
    state = portfolio(database())
    return envelope(state, state.get("last_worker"), "Transactional simulated USD-M isolated-margin ledger")

def explain_decision(index=0):
    state = paper()["data"]
    decisions = state["decisions"]
    return envelope(decisions[-1-index] if index < len(decisions) else
                    dict(action="NO_TRADE", reasons=["NO_DECISION_RECORDED"]), state.get("last_worker"))

def health():
    p = paper()["data"]
    cached = remote("predictions")
    return envelope(dict(status="ok", model_released=(model_version() is not None or bool(cached and cached.get("model_version"))),
                         worker_last_updated=p.get("last_worker"), coverage_ready=coverage()["data"].get("release_ready",False)))


def operations(limit=20):
    from .operations import report
    result=report(database(),database(settings.market_database_url),limit=limit,
        model_version=model_version(),coverage_ready=coverage()['data'].get('release_ready',False))
    return envelope(result,result['checked_at'],'Persistent worker runs and immutable paper ledger')

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
    from .outlook import load_context
    return load_context()


def _ai_allowed():
    """Ask-path Claude calls today (explanations recorded on answers plus parse attempts) under the daily limit."""
    from .answers import ai_calls_today
    now = pd.Timestamp.now(tz='UTC')
    used = ai_calls_today(database(), now, ASK_SOURCES)+_parse_calls_today(now.strftime('%Y-%m-%d'))
    return used < settings.ask_daily_limit


def ask(text, ai=True, source='ask', ctx=None):
    from .answers import record
    from .outlook import answer
    from .questions import rule_parse
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
    from .answers import bucket, scoreboard
    return scoreboard(database()).get(f'{question.type}:{bucket(question.horizon_hours)}')


def outlook_question(question, source='mcp', thread_id=None, run_id=None, ctx=None):
    from .answers import record
    from .outlook import answer
    from .questions import Question
    question = Question.model_validate(question)
    result = answer(question, ctx or _context())
    result['track_record']['forward'] = _forward(question)
    result['id'] = record(database(), result, source, thread_id=thread_id, run_id=run_id)
    return result


def record_view(answer_id, probability, reasons, run_id=None):
    from .answers import set_ai_view
    return dict(recorded=set_ai_view(database(), answer_id, probability, reasons, run_id=run_id))


def answers_view(limit=50):
    from .answers import history, scoreboard
    data = dict(history=history(database(), limit), scoreboard=scoreboard(database()))
    observed = data['history'][0]['asked_at'] if data['history'] else None
    return envelope(data, observed, 'Outlook answers scored after their horizon', max_age=86400)


def chat_send(message, thread_id=None):
    from .chat import send
    return send(database(), message, thread_id)


def chat_threads():
    from .chat import threads
    return envelope(threads(database()), source='In-app chat')


def chat_thread(thread_id):
    from .chat import thread
    return envelope(thread(database(), thread_id), source='In-app chat')
