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


def news(limit=20):
    from .news import snapshot
    data = snapshot(database(), limit)
    observed = max((f['checked_at'] for f in data['feeds']), default=None)
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
    from .forecaster import forecasts, forward_summary, latest_report
    rows = forecasts(database(), limit)
    latest = {}
    for row in rows:
        latest.setdefault(row['horizon'], row)
    _, report = latest_report()
    summary = (report or {}).get('summary', {})
    data = dict(latest=list(latest.values()), recent=rows, forward=forward_summary(database()),
                champions=summary.get('champions', {}), backtest_overfitting=summary.get('backtest_overfitting', {}),
                status='shadow_only_never_trades',
                note='Research forecasts are recorded and scored forward; they never place or size paper trades.')
    observed = max((r['cutoff'] for r in latest.values()), default=None)
    return envelope(data, observed, 'Validated research champion, refitted on recent market history', max_age=1800)

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

