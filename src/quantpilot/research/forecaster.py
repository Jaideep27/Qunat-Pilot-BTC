"""Shadow multi-horizon forecasts from the validated research champions. Records only; never trades.

Direction champions come from the latest direction experiment; range, large-drop and volatility
champions from the latest risk experiment. Every champion was selected on validation only. Models are refitted on recent market history
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
from xgboost import XGBClassifier

from quantpilot.research import events as er
from quantpilot.config import settings
from quantpilot.features.core import build_features, feature_groups
from quantpilot.data.macro import macro_features
from quantpilot.storage.database import Base, database, serialized

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


def code_hash():
    return sha256(Path(__file__).read_bytes()).hexdigest()


def _windows(common, training, train_step):
    lookback = 90 if training == 'rolling90d' else None
    return er.thin(er.split(common, common.max()+pd.Timedelta(minutes=5), rolling_days=lookback), train_step)


def _complete(f, columns, y, labels):
    return f[columns].dropna().index.intersection(y[labels].dropna().index)


def fit(frames, champions, macro_events=None, seed=42, train_step=3, min_days=60):
    """Refit every validated champion on the latest label-complete history (no test window).

    `champions` = dict(direction={horizon: spec}, range={horizon: spec}, drop=spec,
    volatility={horizon: 'ewma'|'garch'}), where spec = dict(group=..., training=...). A 'constant'
    group means nothing beat the base rate (or unconditional quantiles) on validation.
    """
    f, d, mf = _matrix(frames, macro_events)
    groups = _groups(f.columns, list(mf.columns), list(mf.columns[mf.notna().any()]))
    f = f.astype('float32')
    y = er.targets(d.close, d.low)
    regime = (f['regime'].fillna(0) > .5).astype(int)
    horizons = {}
    for horizon in dict.fromkeys([*champions.get('direction', {}), *champions.get('range', {})]):
        up, returns = y[f'up_{horizon}'], y[f'return_{horizon}']
        known = up.dropna()
        # The validated baseline is the whole training history, not a recent sub-window.
        recent = known.index
        model = dict(base_rate=float(up.loc[recent].mean()), training_end=known.index.max().isoformat(),
                     quantiles=np.quantile(returns.loc[recent].dropna(), er.QUANTILES).tolist())
        spec = champions.get('direction', {}).get(horizon, dict(group='constant', training=None))
        model['direction'] = dict(group=spec['group'], training=spec.get('training'), kind='constant')
        if spec['group'] != 'constant':
            columns = groups[spec['group']]
            common = _complete(f, columns, y, [f'up_{horizon}'])
            if len(common) < 288*min_days:
                raise ValueError(f'At least {min_days} days of complete history required for {horizon}')
            w = _windows(common, spec.get('training'), train_step)
            x, fitted, val = f[columns], [], []
            for candidate in (make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=500)),
                              XGBClassifier(n_estimators=60, random_state=seed, eval_metric='logloss', **XGB)):
                candidate.fit(x.loc[w['train']], up.loc[w['train']])
                candidate = CalibratedClassifierCV(FrozenEstimator(candidate), method='sigmoid').fit(
                    x.loc[w['calibration']], up.loc[w['calibration']])
                fitted.append(candidate)
                val.append(candidate.predict_proba(x.loc[w['validation']])[:, 1])
            weights = {}
            for state in (0, 1):
                mask = regime.loc[w['validation']].to_numpy() == state
                if mask.sum() < 288:  # rare conditions shrink back to the overall validation weights
                    mask = np.ones(len(w['validation']), dtype=bool)
                errors = np.array([er.brier(up.loc[w['validation']].to_numpy()[mask], p[mask]) for p in val])
                weights[str(state)] = (np.exp(-errors*10)/np.exp(-errors*10).sum()).tolist()
            model['direction'].update(kind='ensemble', columns=columns, models=fitted, weights=weights,
                                      base_rate=float(up.loc[w['train']].mean()),
                                      training_end=w['validation'].max().isoformat())
        spec = champions.get('range', {}).get(horizon, dict(group='constant', training=None))
        model['range'] = dict(group=spec['group'], training=spec.get('training'), kind='unconditional')
        if spec['group'] != 'constant':
            columns = groups[spec['group']]
            w = _windows(_complete(f, columns, y, [f'return_{horizon}']), spec.get('training'), train_step)
            model['range'].update(kind='model', columns=columns,
                                  model=er.quantile_model(seed).fit(f.loc[w['train'], columns], returns.loc[w['train']]),
                                  training_end=w['validation'].max().isoformat())
        horizons[horizon] = model
    drop = _fit_drop(f, y, groups, champions.get('drop', dict(group='constant', training=None)), seed, train_step)
    hourly = np.log(d.close.iloc[::12]).diff().dropna()
    garch = er.garch_fit(hourly.to_numpy())
    volatility = {h: champions.get('volatility', {}).get(h, 'ewma') for h in horizons}
    version = sha256(json.dumps(dict(champions=champions, ends={h: v['training_end'] for h, v in horizons.items()},
                                     code=code_hash()),
                                sort_keys=True, default=str).encode()).hexdigest()[:12]
    return dict(version=version, fitted_at=datetime.now(timezone.utc).isoformat(), champions=champions,
                horizons=horizons, drop=drop, garch=garch, volatility=volatility, code=code_hash())


def _fit_drop(f, y, groups, spec, seed, train_step):
    """The validated large-drop champion; the recent base rate when none beat it on validation."""
    target = y['drop5_24h']
    known = target.dropna()
    rate = float(known.mean())  # the validated baseline: the whole training history's rate
    if spec.get('group') in (None, 'constant'):
        return dict(kind='constant', group='constant', training=None, rate=rate)
    columns = groups[spec['group']]
    w = _windows(_complete(f, columns, y, ['drop5_24h']), spec.get('training'), train_step)
    tr, ca = w['train'], w['calibration']
    if target.loc[tr].sum() < 20 or target.loc[ca].nunique() < 2:
        return dict(kind='constant', group=spec['group'], training=spec.get('training'), rate=rate,
                    note='too few recent large drops to refit')
    model = XGBClassifier(n_estimators=80, random_state=seed, eval_metric='logloss', **XGB)
    model.fit(f.loc[tr, columns], target.loc[tr])
    model = CalibratedClassifierCV(FrozenEstimator(model), method='sigmoid').fit(f.loc[ca, columns], target.loc[ca])
    return dict(kind='model', group=spec['group'], training=spec.get('training'), columns=columns, model=model, rate=rate)


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
    ewma = float(np.log(d.close).diff().pow(2).ewm(span=288, adjust=False).mean().iloc[-1])
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
                  drop5_model=dict(kind=drop['kind'], group=drop['group'], training=drop.get('training')),
                  news={k: _finite(v) for k, v in nf.iloc[-1].items()})
    out = []
    for horizon, model in bundle['horizons'].items():
        bars = er.HORIZONS[horizon]
        vol = dict(ewma=float(np.sqrt(ewma*bars)),
                   garch=float(np.sqrt(er.garch_forecast(g, variance, shock, max(1, bars//12)))),
                   model=bundle['volatility'][horizon])
        vol['value'] = vol[vol['model']]
        direction, ranges = model['direction'], model['range']
        item = dict(common, horizon=horizon, base_rate=model['base_rate'], volatility=vol,
                    champion=dict(group=direction['group'], training=direction['training'],
                                  training_end=model['training_end']),
                    range_model=dict(kind=ranges['kind'], group=ranges['group'], training=ranges['training']))
        if direction['kind'] == 'constant':
            item.update(status='no_validated_skill', probability_up=model['base_rate'])
        elif row[direction['columns']].isna().any().any():
            item.update(status='missing_inputs', probability_up=None)
        else:
            x = row[direction['columns']]
            p = [m.predict_proba(x)[0, 1] for m in direction['models']]
            item.update(status='forecast', base_rate=direction['base_rate'],
                        probability_up=float(np.dot(direction['weights'][regime], p)))
        if ranges['kind'] == 'model' and not row[ranges['columns']].isna().any().any():
            q = np.sort(ranges['model'].predict(row[ranges['columns']])[0])
            item.update(range_status='model', return_q10=float(q[0]), return_q50=float(q[1]), return_q90=float(q[2]))
        else:
            q = model['quantiles']
            item.update(range_status='unconditional' if ranges['kind'] != 'model' else 'missing_inputs',
                        return_q10=q[0], return_q50=q[1], return_q90=q[2])
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


def champion_reports():
    """Newest direction experiment (summary.champions) and risk experiment (summary.range_champions)."""
    found = {}
    reports = sorted(settings.artifact_dir.glob('research-*/evaluation.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in reports:
        summary = json.loads(path.read_text(encoding='utf-8')).get('summary', {})
        if 'direction' not in found and summary.get('champions'):
            found['direction'] = (path, summary)
        if 'risk' not in found and summary.get('range_champions'):
            found['risk'] = (path, summary)
    return found


CONSTANT = dict(group='constant', training=None)


def promote(champion):
    """Promotion gate, fixed before deployment: a validation-selected champion is used only when its test
    results confirm it (positive mean skill and at least half the folds better). The gate can only reject,
    never choose, so it cannot inflate the reported skill of what is deployed."""
    if champion.get('group') in (None, 'constant'):
        return dict(CONSTANT)
    folds, better = champion.get('folds') or 0, champion.get('folds_beating_constant') or 0
    confirmed = (champion.get('validation_skill') or 0) > 0 and (champion.get('test_skill') or 0) > 0 \
        and folds and 2*better >= folds
    return dict(group=champion['group'], training=champion.get('training')) if confirmed else dict(CONSTANT)


def promote_volatility(champion):
    """GARCH replaces the simpler EWMA only when it was also better in at least half the test windows."""
    if champion.get('model') == 'garch' and 2*champion.get('test_windows_better', 0) >= champion.get('windows', 0):
        return 'garch'
    return 'ewma'


def champion_spec(found):
    """Deployed champions after the promotion gate; missing or rejected parts use the simplest forecast."""
    direction, risk = found.get('direction', (None, {}))[1], found.get('risk', (None, {}))[1]
    return dict(direction={h: promote(c) for h, c in direction.get('champions', {}).items()},
                range={h: promote(c) for h, c in risk.get('range_champions', {}).items()},
                drop=promote(risk['drop_champion']) if risk.get('drop_champion') else dict(CONSTANT),
                volatility={h: promote_volatility(v) for h, v in risk.get('volatility_champions', {}).items()})


def run_cycle(engine=None, now=None):
    from quantpilot.research.event_study import load_market
    from quantpilot.data.macro import events as macro_events
    from quantpilot.ai.news import NewsRun
    engine = engine or database()
    market = database(settings.market_database_url)
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    found = champion_reports()
    if 'direction' not in found:
        return dict(status='no_champion_report')
    champions = champion_spec(found)
    store = settings.artifact_dir/'shadow'/'forecaster.joblib'
    bundle = joblib.load(store) if store.exists() else None
    # Refit when champions or this code change, or the fit is older than the refit interval.
    stale = bundle is None or bundle['champions'] != champions or bundle.get('code') != code_hash() or \
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
                champion_reports={k: str(v[0]) for k, v in found.items()}, cutoff=items[0]['cutoff'] if items else None)
