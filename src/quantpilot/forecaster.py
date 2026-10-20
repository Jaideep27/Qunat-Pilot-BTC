"""Shadow multi-horizon forecasts from the validated research champion. Records only; never trades.

The champion configuration (feature group and training scheme per horizon) comes from the latest
research experiment, selected on validation only. Models are refitted on recent market history
with the same regime-weighted ensemble, and every forecast is scored once its horizon has passed:
that forward record is the only untouched test of whether the forecasts have skill.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import JSON, Column, String, select
from sqlalchemy.orm import Session
from xgboost import XGBClassifier, XGBRegressor

from . import event_research as er
from .config import settings
from .features import build_features, feature_groups
from .macro import macro_features
from .store import Base, database, serialized

XGB = dict(max_depth=3, learning_rate=.05, reg_lambda=8, min_child_weight=50, subsample=.8,
           colsample_bytree=.8, n_jobs=2)


class ShadowForecast(Base):
    __tablename__ = 'shadow_forecasts'
    id = Column(String(160), primary_key=True)
    cutoff = Column(String(40), nullable=False, index=True)
    horizon = Column(String(8), nullable=False, index=True)
    payload = Column(JSON, nullable=False)


def _matrix(frames, macro_events):
    f, _, d = build_features(frames['candles'], frames)
    mf = macro_features(f.index, macro_events or [])
    return f.join(mf), d, mf


def _groups(columns, macro_columns, usable_macro):
    # Macro columns never fall into the base groups (feature_groups files unknown names under technical).
    groups = feature_groups([c for c in columns if c not in macro_columns])
    groups['macro'] = groups['full']+list(usable_macro)
    return groups


def fit(frames, champions, macro_events=None, seed=42, train_step=3, min_days=60):
    """Refit each horizon's champion on the latest label-complete history (no test window)."""
    f, d, mf = _matrix(frames, macro_events)
    groups = _groups(f.columns, list(mf.columns), list(mf.columns[mf.notna().any()]))
    f = f.astype('float32')
    y = er.targets(d.close, d.low)
    regime = (f['regime'].fillna(0) > .5).astype(int)
    horizons = {}
    for horizon, spec in champions.items():
        up, returns = y[f'up_{horizon}'], y[f'return_{horizon}']
        known = up.dropna()
        recent = known.index[known.index >= known.index.max()-pd.Timedelta(days=90)]
        unconditional = np.quantile(returns.loc[recent].dropna(), er.QUANTILES).tolist()
        if spec['group'] == 'constant':
            horizons[horizon] = dict(kind='constant', group='constant', training=None,
                                     base_rate=float(up.loc[recent].mean()), quantiles=unconditional,
                                     training_end=known.index.max().isoformat())
            continue
        columns = groups[spec['group']]
        common = f[columns].dropna().index.intersection(y[[f'up_{horizon}', f'return_{horizon}']].dropna().index)
        if len(common) < 288*min_days:
            raise ValueError(f'At least {min_days} days of complete history required for {horizon}')
        lookback = 90 if spec.get('training') == 'rolling90d' else None
        windows = er.thin(er.split(common, common.max()+pd.Timedelta(minutes=5), rolling_days=lookback), train_step)
        tr, ca, va = windows['train'], windows['calibration'], windows['validation']
        x = f[columns]
        models, val = [], []
        for model in (make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=500)),
                      XGBClassifier(n_estimators=60, random_state=seed, eval_metric='logloss', **XGB)):
            model.fit(x.loc[tr], up.loc[tr])
            model = CalibratedClassifierCV(FrozenEstimator(model), method='sigmoid').fit(x.loc[ca], up.loc[ca])
            models.append(model)
            val.append(model.predict_proba(x.loc[va])[:, 1])
        weights = {}
        for state in (0, 1):
            mask = regime.loc[va].to_numpy() == state
            if mask.sum() < 288:  # rare conditions shrink back to the overall validation weights
                mask = np.ones(len(va), dtype=bool)
            errors = np.array([er.brier(up.loc[va].to_numpy()[mask], p[mask]) for p in val])
            w = np.exp(-errors*10)
            weights[str(state)] = (w/w.sum()).tolist()
        quantile = XGBRegressor(objective='reg:quantileerror', quantile_alpha=np.array(er.QUANTILES),
                                n_estimators=80, random_state=seed, **XGB).fit(x.loc[tr], returns.loc[tr])
        horizons[horizon] = dict(kind='ensemble', group=spec['group'], training=spec.get('training'), columns=columns,
                                 models=models, weights=weights, quantile_model=quantile,
                                 base_rate=float(up.loc[tr].mean()), quantiles=unconditional,
                                 training_end=va.max().isoformat())
    drop = _fit_drop(f, y, groups, champions, seed, train_step)
    hourly = np.log(d.close.iloc[::12]).diff().dropna()
    garch = er.garch_fit(hourly.to_numpy())
    version = sha256(json.dumps(dict(champions=champions, ends={h: v['training_end'] for h, v in horizons.items()},
                                     code=sha256(Path(__file__).read_bytes()).hexdigest()),
                                sort_keys=True).encode()).hexdigest()[:12]
    return dict(version=version, fitted_at=datetime.now(timezone.utc).isoformat(), champions=champions,
                horizons=horizons, drop=drop, garch=garch)


def _fit_drop(f, y, groups, champions, seed, train_step):
    """Large-drop risk uses the 24h champion's features (technical when 24h has no validated skill)."""
    spec = champions.get('24h', {})
    group = spec.get('group') if spec.get('group') not in (None, 'constant') else 'technical'
    target = y['drop5_24h']
    common = f[groups[group]].dropna().index.intersection(target.dropna().index)
    windows = er.thin(er.split(common, common.max()+pd.Timedelta(minutes=5)), train_step)
    tr, ca = windows['train'], windows['calibration']
    rate = float(target.loc[common[common >= common.max()-pd.Timedelta(days=90)]].mean())
    if target.loc[tr].sum() < 20 or target.loc[ca].nunique() < 2:
        return dict(kind='constant', group=group, rate=rate)
    model = XGBClassifier(n_estimators=80, random_state=seed, eval_metric='logloss', **XGB)
    model.fit(f.loc[tr, groups[group]], target.loc[tr])
    model = CalibratedClassifierCV(FrozenEstimator(model), method='sigmoid').fit(f.loc[ca, groups[group]], target.loc[ca])
    return dict(kind='model', group=group, columns=groups[group], model=model, rate=rate)


def _finite(value):
    return None if value is None or not np.isfinite(value) else float(value)


def predict(bundle, frames, macro_events=None, runs=None, as_of=None):
    """Forecasts at the latest completed bar (or `as_of`), using only rows known at that cutoff."""
    f, d, _ = _matrix(frames, macro_events)
    nf = er.news_features(f.index, runs or [], provider=settings.news_reasoners[0])
    if as_of is not None:
        f, d, nf = f.loc[:as_of], d.loc[:as_of], nf.loc[:as_of]
    t = f.index[-1]
    row = f.iloc[[-1]].astype('float32')
    regime = str(int((row['regime'].fillna(0) > .5).iloc[0]))
    log_ret = np.log(d.close).diff()
    ewma = float(log_ret.pow(2).ewm(span=288, adjust=False).mean().iloc[-1])
    hourly = np.log(d.close.iloc[::-12].iloc[::-1]).diff().dropna()  # hourly closes ending at the cutoff
    g = bundle['garch']
    variance = er.garch_filter(hourly.to_numpy(), g['omega'], g['alpha'], g['beta'])[-1] if len(hourly) else np.nan
    shock = float(hourly.iloc[-1]) if len(hourly) else 0.0
    drop = bundle['drop']
    if drop['kind'] == 'model' and not row[drop['columns']].isna().any().any():
        drop_risk = float(drop['model'].predict_proba(row[drop['columns']])[0, 1])
    else:
        drop_risk = drop['rate']
    common = dict(cutoff=t.isoformat(), model_version=bundle['version'], close=float(d.close.iloc[-1]),
                  regime='trending' if regime == '1' else 'ranging', drop5_risk=drop_risk,
                  drop5_model=drop['kind'], news={k: _finite(v) for k, v in nf.iloc[-1].items()})
    out = []
    for horizon, model in bundle['horizons'].items():
        bars = er.HORIZONS[horizon]
        vol = dict(ewma=float(np.sqrt(ewma*bars)),
                   garch=float(np.sqrt(er.garch_forecast(g, variance, shock, max(1, bars//12)))))
        item = dict(common, horizon=horizon, base_rate=model['base_rate'], volatility=vol,
                    champion=dict(group=model['group'], training=model['training'], training_end=model['training_end']))
        if model['kind'] == 'constant':
            q = model['quantiles']
            item.update(status='no_validated_skill', probability_up=model['base_rate'],
                        return_q10=q[0], return_q50=q[1], return_q90=q[2])
        elif row[model['columns']].isna().any().any():
            item.update(status='missing_inputs', probability_up=None, return_q10=None, return_q50=None, return_q90=None)
        else:
            x = row[model['columns']]
            probabilities = [m.predict_proba(x)[0, 1] for m in model['models']]
            q = np.sort(model['quantile_model'].predict(x)[0])
            item.update(status='forecast', probability_up=float(np.dot(model['weights'][regime], probabilities)),
                        return_q10=float(q[0]), return_q50=float(q[1]), return_q90=float(q[2]))
        out.append(item)
    return out


def record(engine, items):
    added = 0
    with serialized(engine) as session:
        for item in items:
            key = f"{item['model_version']}:{item['horizon']}:{item['cutoff']}"
            if session.get(ShadowForecast, key):
                continue
            session.add(ShadowForecast(id=key, cutoff=item['cutoff'], horizon=item['horizon'], payload=item))
            added += 1
    return added


def evaluate(engine, closes, now=None):
    """Score forecasts whose horizon has passed against realized closes (indexed by bar close time)."""
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    scored = 0
    with serialized(engine) as session:
        for row in session.scalars(select(ShadowForecast)).all():
            item = row.payload
            if 'outcome' in item:
                continue
            cutoff = pd.Timestamp(item['cutoff'])
            end = cutoff+pd.Timedelta(minutes=5*er.HORIZONS[item['horizon']])
            if end > now or end not in closes.index:
                continue
            start = float(closes.get(cutoff, item['close']))
            realized = float(closes.loc[end])/start-1
            up = float(realized > 0)
            outcome = {'return': realized, 'up': up, 'evaluated_at': now.isoformat()}
            if item.get('probability_up') is not None:
                outcome['brier'] = (up-item['probability_up'])**2
                outcome['constant_brier'] = (up-item['base_rate'])**2
            if item.get('return_q10') is not None:
                outcome['inside_80'] = bool(item['return_q10'] <= realized <= item['return_q90'])
            row.payload = {**item, 'outcome': outcome}
            scored += 1
    return scored


def forecasts(engine=None, limit=200):
    with Session(engine or database()) as session:
        rows = session.scalars(select(ShadowForecast).order_by(ShadowForecast.cutoff.desc()).limit(limit)).all()
        return [dict(id=r.id, **r.payload) for r in rows]


def forward_summary(engine=None):
    """Forward (never previously observed) skill per horizon, from scored forecasts only."""
    result = {}
    with Session(engine or database()) as session:
        rows = [r.payload for r in session.scalars(select(ShadowForecast)).all()]
    for horizon in er.HORIZONS:
        mine = [r for r in rows if r['horizon'] == horizon]
        if not mine:
            continue
        scored = [r['outcome'] for r in mine if 'outcome' in r]
        briers = [o for o in scored if 'brier' in o]
        inside = [o['inside_80'] for o in scored if 'inside_80' in o]
        result[horizon] = dict(scored=len(scored), pending=len(mine)-len(scored),
                               brier=float(np.mean([o['brier'] for o in briers])) if briers else None,
                               constant_brier=float(np.mean([o['constant_brier'] for o in briers])) if briers else None,
                               interval_coverage_80=float(np.mean(inside)) if inside else None,
                               note='Overlapping forecasts share future windows; treat early counts as indicative.')
    return result


def latest_report():
    """Newest research experiment that selected champions (validation-only selection)."""
    reports = sorted(settings.artifact_dir.glob('research-*/evaluation.json'), key=lambda p: p.stat().st_mtime)
    for path in reversed(reports):
        report = json.loads(path.read_text(encoding='utf-8'))
        if report.get('summary', {}).get('champions'):
            return path, report
    return None, None


def run_cycle(engine=None, now=None):
    from .event_study import load_market
    from .macro import events as macro_events
    from .news import NewsRun
    engine = engine or database()
    market = database(settings.market_database_url)
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    path, report = latest_report()
    if report is None:
        return dict(status='no_champion_report')
    champions = {h: dict(group=c['group'], training=c['training']) for h, c in report['summary']['champions'].items()}
    store = settings.artifact_dir/'shadow'/'forecaster.joblib'
    bundle = joblib.load(store) if store.exists() else None
    stale = bundle is None or bundle['champions'] != champions or \
        now-pd.Timestamp(bundle['fitted_at']) > pd.Timedelta(hours=settings.forecast_refit_hours)
    events = macro_events()
    with Session(engine) as session:
        runs = [dict(status=r.status, **r.payload) for r in session.scalars(select(NewsRun)).all()]
    if stale:
        history = load_market(market, now-pd.Timedelta(days=settings.forecast_history_days+40), now)
        bundle = fit(history, champions, events)
        del history
        store.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(bundle, store.with_suffix('.partial'))
        store.with_suffix('.partial').replace(store)
    recent = load_market(market, now-pd.Timedelta(days=45), now)
    items = predict(bundle, recent, events, runs)
    added = record(engine, items)
    closes = recent['candles'].set_index('available_at').close.astype(float)
    scored = evaluate(engine, closes, now)
    return dict(status='recorded', model_version=bundle['version'], refitted=stale, added=added, scored=scored,
                champion_report=str(path), cutoff=items[0]['cutoff'] if items else None)
