"""Causal event features and multi-horizon research. Never publishes a trading model.

Components, all fitted inside each walk-forward window and compared on identical timestamps:
direction (calibrated logistic + XGBoost, regime-weighted), return ranges (multi-quantile
XGBoost), large-drop risk (touching -5% within 24h), and volatility (EWMA vs GARCH(1,1)).
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.signal import lfilter
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, mean_pinball_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor

from quantpilot.config import settings
from quantpilot.data.archives import load_frames, dataset_hashes
from quantpilot.features.core import build_features, feature_groups

HORIZONS = {'1h': 12, '4h': 48, '24h': 288}
PURGE = pd.Timedelta(hours=24)
QUANTILES = (.1, .5, .9)
DROP = .05


def news_features(index, runs, provider=None):
    """Rolling evidence available AFTER actual reasoning completion.

    Unknown before the first successful forward extraction; no zero-filled
    historical sentiment. Results backfilled by today's LLM are excluded.
    """
    records = []
    for run in runs:
        if run['status'] != 'completed' or run.get('retrospective', True):
            continue
        # One reasoner only: several reasoners analyze the same items and must not be counted twice.
        if provider is not None and (run.get('provider') or 'codex') != provider:
            continue
        t = pd.Timestamp(run['available_at'])
        for event in run.get('result', {}).get('events', []):
            if event['relevance'] == 'irrelevant':
                continue
            score = {'positive': 1., 'negative': -1., 'mixed': 0., 'uncertain': 0.}[event['sentiment']]
            records.append((t, score, event['sentiment'] == 'uncertain'))
    out = pd.DataFrame(np.nan, index=index,
                       columns=['news_count_1h', 'news_tone_1h', 'news_uncertain_1h'])
    if not records:
        return out
    events = pd.DataFrame(records, columns=['available_at', 'tone', 'uncertain']).set_index('available_at')
    # Count only observations in (cutoff - 1h, cutoff]. Full grid avoids future backfill.
    grouped = events.groupby(level=0).agg(count=('tone', 'size'), tone=('tone', 'sum'), uncertain=('uncertain', 'sum'))
    grid = grouped.index.union(index).sort_values()
    sums = grouped.reindex(grid, fill_value=0).rolling('1h', closed='right').sum().reindex(index)
    out.loc[index >= grouped.index.min()] = sums.loc[index >= grouped.index.min()].to_numpy()
    return out


def targets(close, low=None):
    if close.index.tz is None or close.index.has_duplicates or not close.index.is_monotonic_increasing:
        raise ValueError('unique ordered timezone-aware cutoffs required')
    if (close.index.to_series().diff().dropna() != pd.Timedelta(minutes=5)).any():
        raise ValueError('targets require contiguous five-minute cutoffs')
    result = pd.DataFrame(index=close.index)
    for label, bars in HORIZONS.items():
        forward = close.shift(-bars)/close-1
        result[f'return_{label}'] = forward
        result[f'up_{label}'] = (forward > 0).astype(float).where(forward.notna())
    # Touching -5% at any point in the next 24h, from bars strictly after the cutoff.
    bars = HORIZONS['24h']
    lows = close if low is None else low
    future_low = lows.iloc[::-1].rolling(bars, min_periods=bars).min().iloc[::-1].shift(-1)
    drawdown = future_low/close-1
    result['drop5_24h'] = (drawdown <= -DROP).astype(float).where(drawdown.notna())
    return result


def split(index, test_start, test_days=14, rolling_days=None):
    start = pd.Timestamp(test_start)
    validation_start = start-pd.Timedelta(days=14)
    calibration_start = validation_start-pd.Timedelta(days=14)
    lower = index.min() if rolling_days is None else calibration_start-pd.Timedelta(days=rolling_days)
    return dict(train=index[(index >= lower) & (index < calibration_start-PURGE)],
                calibration=index[(index >= calibration_start) & (index < validation_start-PURGE)],
                validation=index[(index >= validation_start) & (index < start-PURGE)],
                test=index[(index >= start) & (index < start+pd.Timedelta(days=test_days)-PURGE)])


def thin(windows, step=3):
    """Fit on every `step`-th cutoff: adjacent 5-minute cutoffs share almost the same future label window.
    Validation and test keep every cutoff so evaluation is unchanged."""
    return {k: (v[::step] if k in ('train', 'calibration') else v) for k, v in windows.items()}


def brier(y, p):
    return float(brier_score_loss(y, p))


def block_bootstrap_skill(y, p, base, index, seed=0, draws=1000):
    """Brier improvement of `p` over `base`, resampling whole UTC days (overlapping labels stay together)."""
    y, p, base = (np.asarray(v, dtype=float) for v in (y, p, base))
    days = pd.DatetimeIndex(index).floor('D')
    codes, uniques = pd.factorize(days)
    gain = (base-y)**2-(p-y)**2
    sums, counts = np.bincount(codes, gain), np.bincount(codes)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(uniques), (draws, len(uniques)))
    samples = sums[picks].sum(axis=1)/counts[picks].sum(axis=1)
    return dict(brier_improvement=float(gain.mean()), blocks=int(len(uniques)),
                ci95=[float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))])


def garch_filter(returns, omega, alpha, beta, initial=None):
    """Conditional variances: var_t = omega + alpha*r_{t-1}^2 + beta*var_{t-1}."""
    r2 = np.asarray(returns, dtype=float)**2
    start = float(np.mean(r2)) if initial is None else initial
    drive = omega+alpha*np.r_[start, r2[:-1]]
    var, _ = lfilter([1.], [1., -beta], drive, zi=[beta*start])
    return var


def garch_fit(returns):
    """Gaussian GARCH(1,1) maximum likelihood with variance targeting (omega from the sample variance)."""
    returns = np.asarray(returns, dtype=float)
    returns = returns[np.isfinite(returns)]
    sample = float(np.mean(returns**2))
    def loss(theta):
        alpha, beta = theta
        if alpha < 0 or beta < 0 or alpha+beta >= .999:
            return 1e12
        var = np.maximum(garch_filter(returns, sample*(1-alpha-beta), alpha, beta, sample), 1e-300)
        return float(np.sum(np.log(var)+returns**2/var))
    best = minimize(loss, x0=[.05, .9], method='Nelder-Mead', options=dict(xatol=1e-5, fatol=1e-3, maxiter=600))
    alpha, beta = (float(v) for v in best.x)
    return dict(omega=sample*(1-alpha-beta), alpha=alpha, beta=beta, converged=bool(best.success))


def garch_forecast(params, variance, shock, steps):
    """Sum of the next `steps` conditional variances given the latest variance and return shock."""
    omega, persistence = params['omega'], params['alpha']+params['beta']
    v = omega+params['alpha']*shock**2+params['beta']*variance
    total = 0.0
    for _ in range(steps):
        total += v
        v = omega+persistence*v
    return total


def qlike(realized, forecast):
    ratio = np.asarray(realized, dtype=float)/np.asarray(forecast, dtype=float)
    ratio = ratio[np.isfinite(ratio) & (ratio > 0)]
    return float(np.mean(ratio-np.log(ratio)-1))


def volatility_scores(close, train, idx, bars, fitted=None):
    """QLIKE of EWMA and GARCH(1,1) (fitted on hourly returns inside `train` only) on cutoffs `idx`."""
    log_ret = np.log(close).diff()
    hourly = np.log(close.iloc[::12]).diff().dropna()
    fitted = fitted or garch_fit(hourly.loc[(hourly.index >= train.min()) & (hourly.index <= train.max())].to_numpy())
    hourly_var = pd.Series(garch_filter(hourly.to_numpy(), fitted['omega'], fitted['alpha'], fitted['beta']),
                           index=hourly.index)
    last_shock, last_var = hourly.reindex(idx, method='ffill'), hourly_var.reindex(idx, method='ffill')
    ewma = log_ret.pow(2).ewm(span=288, adjust=False).mean().loc[idx].to_numpy()*bars
    realized = log_ret.pow(2).iloc[::-1].rolling(bars, min_periods=bars).sum().iloc[::-1].shift(-1).loc[idx].to_numpy()
    garch = np.array([garch_forecast(fitted, v, s, max(1, bars//12)) for v, s in zip(last_var, last_shock)])
    ok = np.isfinite(realized) & np.isfinite(garch) & np.isfinite(ewma)
    return dict(qlike_ewma=qlike(realized[ok], ewma[ok]), qlike_garch=qlike(realized[ok], garch[ok]),
                coverage90_ewma=_coverage(close, idx, bars, ewma, ok),
                coverage90_garch=_coverage(close, idx, bars, garch, ok), garch=fitted)


def volatility_comparison(close, windows, horizons):
    """EWMA (5-minute, span 1 day) vs GARCH(1,1) fitted on hourly returns from the training window only."""
    log_ret = np.log(close).diff()
    hourly = np.log(close.iloc[::12]).diff().dropna()
    rows = []
    for name, (train, test) in windows.items():
        fitted = garch_fit(hourly.loc[(hourly.index >= train.min()) & (hourly.index <= train.max())].to_numpy())
        # Filter the whole history with frozen parameters; each hourly variance uses only prior returns.
        hourly_var = pd.Series(garch_filter(hourly.to_numpy(), fitted['omega'], fitted['alpha'], fitted['beta']),
                               index=hourly.index)
        last_shock = hourly.reindex(test, method='ffill')
        last_var = hourly_var.reindex(test, method='ffill')
        ewma = log_ret.pow(2).ewm(span=288, adjust=False).mean().loc[test]
        for label, bars in horizons.items():
            realized = log_ret.pow(2).iloc[::-1].rolling(bars, min_periods=bars).sum().iloc[::-1].shift(-1).loc[test]
            hours = max(1, bars//12)
            garch = np.array([garch_forecast(fitted, v, s, hours) for v, s in zip(last_var, last_shock)])
            ewma_h = ewma.to_numpy()*bars
            ok = np.isfinite(realized.to_numpy()) & np.isfinite(garch) & np.isfinite(ewma_h)
            realized_ok = realized.to_numpy()[ok]
            rows.append(dict(window=name, horizon=label, garch=fitted,
                             qlike_ewma=qlike(realized_ok, ewma_h[ok]), qlike_garch=qlike(realized_ok, garch[ok]),
                             coverage90_ewma=_coverage(close, test, bars, ewma_h, ok),
                             coverage90_garch=_coverage(close, test, bars, garch, ok)))
    return dict(models=['ewma', 'garch'], rows=rows,
                note='QLIKE on realized variance of future 5-minute log returns; lower is better.')


def _coverage(close, test, bars, variance, ok):
    forward = (close.shift(-bars)/close-1).loc[test].to_numpy()
    good = ok & np.isfinite(forward)
    return float((np.abs(forward[good]) <= 1.645*np.sqrt(variance[good])).mean()) if good.any() else None


def quantile_model(seed):
    return XGBRegressor(objective='reg:quantileerror', quantile_alpha=np.array(QUANTILES), n_estimators=80,
                        max_depth=3, learning_rate=.05, reg_lambda=8, min_child_weight=50, subsample=.8,
                        colsample_bytree=.8, n_jobs=2, random_state=seed)


def range_scores(model, x, y, idx, base):
    """Pinball loss of fitted quantiles vs fixed training quantiles `base`, and 80% interval coverage."""
    predicted = np.sort(model.predict(x.loc[idx]), axis=1)  # prevent crossing quantiles
    actual = y.loc[idx].to_numpy()
    pinball = float(np.mean([mean_pinball_loss(actual, predicted[:, i], alpha=a) for i, a in enumerate(QUANTILES)]))
    constant = float(np.mean([mean_pinball_loss(actual, np.repeat(base[i], len(actual)), alpha=a)
                              for i, a in enumerate(QUANTILES)]))
    return dict(pinball=pinball, constant_pinball=constant,
                interval_coverage_80=float(((actual >= predicted[:, 0]) & (actual <= predicted[:, 2])).mean()),
                median_interval_width=float(np.median(predicted[:, 2]-predicted[:, 0])))


def quantile_ranges(x, y, tr, te, seed):
    """Multi-quantile XGBoost versus unconditional training quantiles (pinball loss; 80% interval coverage)."""
    model = quantile_model(seed).fit(x.loc[tr], y.loc[tr])
    return model, range_scores(model, x, y, te, np.quantile(y.loc[tr], QUANTILES))


def drop_risk(x, target, windows, seed):
    """Calibrated large-drop classifier; alerts at the validation 90th percentile of predicted risk."""
    tr, ca, va, te = (windows[k] for k in ('train', 'calibration', 'validation', 'test'))
    y = target
    if y.loc[tr].sum() < 20 or y.loc[ca].nunique() < 2:
        return None, dict(status='skipped', reason='too few large drops to fit and calibrate',
                          train_positives=int(y.loc[tr].sum()))
    model = XGBClassifier(n_estimators=80, max_depth=3, learning_rate=.05, reg_lambda=8, min_child_weight=50, subsample=.8, colsample_bytree=.8, n_jobs=2,
                          random_state=seed, eval_metric='logloss')
    model.fit(x.loc[tr], y.loc[tr])
    model = CalibratedClassifierCV(FrozenEstimator(model), method='sigmoid').fit(x.loc[ca], y.loc[ca])
    threshold = float(np.quantile(model.predict_proba(x.loc[va])[:, 1], .9))
    p = model.predict_proba(x.loc[te])[:, 1]
    actual = y.loc[te].to_numpy()
    alerts = p >= threshold
    base = np.repeat(float(y.loc[tr].mean()), len(actual))
    return model, dict(status='evaluated', base_rate_train=float(base[0]), test_rate=float(actual.mean()),
                       brier=brier(actual, p), constant_brier=brier(actual, base),
                       auc=float(roc_auc_score(actual, p)) if len(set(actual)) > 1 else None,
                       alert_threshold=threshold, alert_share=float(alerts.mean()),
                       precision=float(actual[alerts].mean()) if alerts.any() else None,
                       recall=float(actual[alerts].sum()/actual.sum()) if actual.sum() else None,
                       skill=block_bootstrap_skill(actual, p, base, te, seed))


def _progress(output, config):
    """Resumable progress: refuse a different configuration; return finished results and an appender."""
    progress, done = output/'progress.jsonl', {}
    if output.exists():
        if not progress.exists():
            raise FileExistsError('Output exists without resumable progress; choose a new directory')
        lines = progress.read_text(encoding='utf-8').splitlines()
        if json.loads(lines[0]).get('config') != config:
            raise ValueError('Existing progress was produced by a different configuration or code version')
        done = {row['id']: row for row in map(json.loads, lines[1:])}
    else:
        output.mkdir(parents=True)
        progress.write_text(json.dumps({'config': config})+'\n', encoding='utf-8')
    def record(row):
        with progress.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, default=str)+'\n')
    return done, record


def _prepare(frames, runs, macro_events, groups, horizons, min_days, reserve_months, folds):
    """Features, labels and the common eligible timestamps every configuration is compared on."""
    from quantpilot.data.macro import macro_features
    frames = frames if frames is not None else load_frames(settings.data_dir)
    if runs is None:
        from sqlalchemy import select
        from sqlalchemy.orm import Session
        from quantpilot.ai.news import NewsRun
        from quantpilot.storage.database import database
        with Session(database()) as session:
            runs = [dict(status=r.status, **r.payload) for r in session.scalars(select(NewsRun)).all()]
    if macro_events is None:
        from quantpilot.data.macro import events as stored_macro
        macro_events = stored_macro()
    f, _, d = build_features(frames['candles'], frames)
    y = targets(d.close, d.low)
    nf = news_features(f.index, runs, provider=settings.news_reasoners[0])
    mf = macro_features(f.index, macro_events)
    mf = mf.loc[:, mf.notna().any()]  # kinds absent from the calendar contribute no columns
    group_columns = feature_groups(list(f.columns))
    group_columns['macro'] = group_columns['full']+list(mf.columns)
    f = f.join(nf).join(mf).astype('float32')  # halves memory; XGBoost works in float32 anyway
    used = sorted({c for g in groups for c in group_columns[g]})
    labels = [f'up_{h}' for h in horizons]+[f'return_{h}' for h in horizons]+['drop5_24h']
    common = f[used].dropna().index.intersection(y[labels].dropna().index)
    if len(common) < 288*min_days:
        raise ValueError(f'At least {min_days} days of common eligible history required')
    end = common.max()-pd.DateOffset(months=reserve_months) if reserve_months else common.max()
    test_starts = [end-pd.Timedelta(days=14*(i+1)) for i in reversed(range(folds))]
    return dict(f=f, y=y, d=d, nf=nf, mf=mf, group_columns=group_columns, common=common, end=end,
                test_starts=test_starts)


def train_experiment(output, folds=4, frames=None, runs=None, macro_events=None, min_days=90,
                     groups=('price', 'technical', 'derivatives', 'full', 'macro'), horizons=tuple(HORIZONS),
                     reserve_months=2, seed=42, train_step=3):
    """New research directory, or resume of an interrupted run with the identical configuration.

    The last `reserve_months` are left unused for later forward-design checks. No holdout is
    relabeled 'untouched' and no model is released or allowed to trade. Every finished result
    is appended to progress.jsonl, so a terminated run resumes instead of refitting.
    """
    output = Path(output)
    config = dict(folds=folds, min_days=min_days, groups=list(groups), horizons=list(horizons),
                  reserve_months=reserve_months, seed=seed, train_step=train_step,
                  code=sha256(Path(__file__).read_bytes()).hexdigest())
    done, record = _progress(output, config)
    supplied = frames is not None
    data = _prepare(frames, runs, macro_events, groups, horizons, min_days, reserve_months, folds)
    f, y, d, nf, mf = (data[k] for k in ('f', 'y', 'd', 'nf', 'mf'))
    group_columns, common, end, test_starts = (data[k] for k in ('group_columns', 'common', 'end', 'test_starts'))
    rows, risk, artifacts, vol_windows = [], [], {}, {}
    for fold, test_start in enumerate(test_starts):
        for strategy, lookback in (('expanding', None), ('rolling90d', 90)):
            windows = split(common, test_start, rolling_days=lookback)
            if min(map(len, windows.values())) < 288:
                continue
            windows = thin(windows, train_step)
            tr, ca, va, te = [windows[k] for k in ('train', 'calibration', 'validation', 'test')]
            if strategy == 'expanding':
                vol_windows[f'fold{fold}'] = (tr, te)
            regime = (f['regime'].fillna(0) > .5).astype(int)
            for group in groups:
                columns = group_columns[group]
                x = f[columns]
                for horizon in horizons:
                    key = f'{fold}-{strategy}-{group}-{horizon}'
                    if key in done:
                        rows.append(done[key])
                        continue
                    target = y['up_'+horizon]
                    if min(target.loc[tr].nunique(), target.loc[ca].nunique()) < 2:
                        continue
                    models = [make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=500)),
                              XGBClassifier(n_estimators=60, max_depth=3, learning_rate=.05,
                                            reg_lambda=8, min_child_weight=50, subsample=.8, colsample_bytree=.8, n_jobs=2, random_state=seed, eval_metric='logloss')]
                    calibrated, vp, tp = [], [], []
                    for model in models:
                        model.fit(x.loc[tr], target.loc[tr])
                        model = CalibratedClassifierCV(FrozenEstimator(model), method='sigmoid')
                        model.fit(x.loc[ca], target.loc[ca])
                        calibrated.append(model)
                        vp.append(model.predict_proba(x.loc[va])[:, 1])
                        tp.append(model.predict_proba(x.loc[te])[:, 1])
                    weights = {}
                    combined, combined_val = np.zeros(len(te)), np.zeros(len(va))
                    for state in (0, 1):
                        mask = (regime.loc[va].to_numpy() == state)
                        # Small conditions shrink back to overall validation.
                        if mask.sum() < 288:
                            mask = np.ones(len(va), dtype=bool)
                        errors = np.array([brier(target.loc[va].to_numpy()[mask], p[mask]) for p in vp])
                        w = np.exp(-errors*10); w /= w.sum()
                        weights[str(state)] = w.tolist()
                        test_mask = regime.loc[te].to_numpy() == state
                        combined[test_mask] = np.array(tp)[:, test_mask].T @ w
                        val_mask = regime.loc[va].to_numpy() == state
                        combined_val[val_mask] = np.array(vp)[:, val_mask].T @ w
                    returns = y['return_'+horizon]
                    ridge = make_pipeline(StandardScaler(), Ridge(alpha=100.))
                    ridge.fit(x.loc[tr], returns.loc[tr])
                    expected = ridge.predict(x.loc[te])
                    quantile_model, ranges = quantile_ranges(x, returns, tr, te, seed)
                    constant = np.repeat(target.loc[tr].mean(), len(te))
                    row = dict(id=key, fold=fold, training=strategy, group=group, horizon=horizon,
                        train_rows=len(tr), test_rows=len(te),
                        windows={k: [v.min().isoformat(), v.max().isoformat()] for k, v in windows.items()},
                        weights=weights, brier=brier(target.loc[te], combined),
                        validation_brier=brier(target.loc[va], combined_val),
                        validation_constant_brier=brier(target.loc[va], np.repeat(target.loc[tr].mean(), len(va))),
                        constant_brier=brier(target.loc[te], constant),
                        auc=float(roc_auc_score(target.loc[te], combined)) if target.loc[te].nunique() > 1 else None,
                        skill_vs_constant=block_bootstrap_skill(target.loc[te], combined, constant, te, seed),
                        return_mae=float(np.mean(np.abs(expected-returns.loc[te]))),
                        zero_return_mae=float(np.mean(np.abs(returns.loc[te]))), **ranges)
                    artifacts[key] = dict(models=calibrated, weights=weights, return_model=ridge,
                                          quantile_model=quantile_model, columns=columns)
                    rows.append(row)
                    record(row)
                    print(json.dumps({'completed': key, 'brier': row['brier'], 'constant': row['constant_brier']}),
                          flush=True)
                key = f'{fold}-{strategy}-{group}-drop5'
                if key in done:
                    risk.append(done[key])
                    continue
                model, result = drop_risk(x, y['drop5_24h'], windows, seed)
                risk.append(dict(id=key, fold=fold, training=strategy, group=group, **result))
                record(risk[-1])
                if model is not None:
                    artifacts[key] = dict(model=model, columns=columns)
    volatility = volatility_comparison(d.close, vol_windows, {h: HORIZONS[h] for h in horizons})
    report = dict(status='research_only', promotion_allowed=False, seed=seed, purge_hours=24, train_step=train_step,
        data_hashes='supplied_frames' if supplied else dataset_hashes(settings.data_dir),
        code_hash=sha256(Path(__file__).read_bytes()).hexdigest(),
        eligible_rows=len(common), reserved_after=end.isoformat(),
        news_eligible_rows=int(nf.dropna().shape[0]), macro_columns=list(mf.columns),
        news_experiment_status='blocked_until_forward_news_history_and_agent_validation',
        historical_test_status='Previously inspected data; not an untouched holdout',
        incomplete=['news ablation (needs forward news history)', 'cost-aware shared-engine replay',
                    'fresh forward validation'],
        resumed_results=len(done), models_saved=sorted(artifacts),
        comparisons=rows, drop_risk=risk, volatility=volatility, summary=summarize(rows, risk, volatility))
    (output/'evaluation.json').write_text(json.dumps(report, indent=2, allow_nan=False, default=str), encoding='utf-8')
    (output/'report.md').write_text(render(report), encoding='utf-8')
    joblib.dump(artifacts, output/'candidates.joblib')
    return report


def _skills(rows, horizon, kind, metric='brier'):
    """Per (group, training): fold -> skill, where skill = constant loss minus model loss (lower loss is better)."""
    out = {}
    for r in rows:
        if r['horizon'] != horizon or r.get(f'{kind}{metric}') is None:
            continue
        out.setdefault((r['group'], r['training']), {})[r['fold']] = r[f'{kind}constant_{metric}']-r[f'{kind}{metric}']
    return out


def champions(rows, metric='brier'):
    """Per horizon, the configuration with the best mean VALIDATION skill; test results are only reported.

    If nothing beats the constant base rate on validation, the champion is the constant forecast.
    """
    result = {}
    for horizon in dict.fromkeys(r['horizon'] for r in rows):
        validation, test = _skills(rows, horizon, 'validation_', metric), _skills(rows, horizon, '', metric)
        if not validation:
            continue
        mean = {k: float(np.mean(list(v.values()))) for k, v in validation.items()}
        best = max(mean, key=mean.get)
        if mean[best] <= 0:
            result[horizon] = dict(group='constant', training=None, validation_skill=0.0, test_skill=0.0,
                                   folds_beating_constant=None, configurations_tried=len(mean),
                                   note='No configuration beat the base rate on validation')
            continue
        scores = list(test.get(best, {}).values())
        result[horizon] = dict(group=best[0], training=best[1], validation_skill=mean[best],
                               test_skill=float(np.mean(scores)) if scores else None,
                               folds_beating_constant=int(sum(s > 0 for s in scores)), folds=len(scores),
                               configurations_tried=len(mean))
    return result


def backtest_overfitting(rows, metric='brier'):
    """Probability of backtest overfitting: share of folds whose validation pick ranks at or below the
    median of that fold's test results (rank logit <= 0), after Bailey et al.'s CSCV idea."""
    result = {}
    for horizon in dict.fromkeys(r['horizon'] for r in rows):
        validation, test = _skills(rows, horizon, 'validation_', metric), _skills(rows, horizon, '', metric)
        folds = sorted({f for v in validation.values() for f in v})
        logits = []
        for fold in folds:
            configs = [k for k in validation if fold in validation[k] and fold in test.get(k, {})]
            if len(configs) < 2:
                continue
            pick = max(configs, key=lambda k: validation[k][fold])
            ordered = sorted(configs, key=lambda k: test[k][fold])  # rank 1 = worst out of sample
            omega = (ordered.index(pick)+1)/(len(configs)+1)
            logits.append(float(np.log(omega/(1-omega))))
        result[horizon] = dict(folds=len(logits), pbo=float(np.mean([l <= 0 for l in logits])) if logits else None,
                               rank_logits=logits)
    return result


def volatility_champions(rows):
    """Per horizon, EWMA or GARCH by lower mean VALIDATION QLIKE; test QLIKE is only reported."""
    out = {}
    for horizon in dict.fromkeys(r['horizon'] for r in rows):
        mine = [r for r in rows if r['horizon'] == horizon]
        val = {m: float(np.mean([r[f'validation_qlike_{m}'] for r in mine])) for m in ('ewma', 'garch')}
        best = min(val, key=val.get)
        other = 'ewma' if best == 'garch' else 'garch'
        out[horizon] = dict(model=best, validation_qlike=val[best], validation_qlike_alternative=val[other],
                            test_qlike=float(np.mean([r[f'qlike_{best}'] for r in mine])),
                            test_qlike_alternative=float(np.mean([r[f'qlike_{other}'] for r in mine])),
                            test_windows_better=int(sum(r[f'qlike_{best}'] < r[f'qlike_{other}'] for r in mine)),
                            windows=len(mine))
    return out


def summarize(rows, risk, volatility):
    frame = pd.DataFrame(rows)
    direction = []
    if not frame.empty:
        grouped = frame.groupby(['group', 'horizon', 'training'])
        for (group, horizon, training), part in grouped:
            direction.append(dict(group=group, horizon=horizon, training=training, folds=int(len(part)),
                                  mean_brier=float(part.brier.mean()), mean_constant_brier=float(part.constant_brier.mean()),
                                  folds_beating_constant=int((part.brier < part.constant_brier).sum()),
                                  mean_auc=float(part.auc.dropna().mean()) if part.auc.notna().any() else None,
                                  mean_pinball=float(part.pinball.mean()), mean_constant_pinball=float(part.constant_pinball.mean()),
                                  mean_interval_coverage_80=float(part.interval_coverage_80.mean())))
    evaluated = [r for r in risk if r.get('status') == 'evaluated']
    vol = pd.DataFrame(volatility['rows'])
    return dict(direction=direction, champions=champions(rows), backtest_overfitting=backtest_overfitting(rows),
                drop_risk=dict(evaluated=len(evaluated), skipped=len(risk)-len(evaluated),
                               beats_constant=sum(r['brier'] < r['constant_brier'] for r in evaluated),
                               mean_auc=float(np.nanmean([r['auc'] for r in evaluated if r['auc'] is not None]))
                               if any(r['auc'] is not None for r in evaluated) else None),
                volatility=[dict(horizon=h, garch_wins=int((p.qlike_garch < p.qlike_ewma).sum()), windows=int(len(p)),
                                 mean_qlike_ewma=float(p.qlike_ewma.mean()), mean_qlike_garch=float(p.qlike_garch.mean()))
                            for h, p in vol.groupby('horizon')] if not vol.empty else [])


def render(report):
    s = report['summary']
    lines = ['# Multi-horizon forecast experiment', '',
             f"Research only; promotion allowed: **{report['promotion_allowed']}**. Eligible cutoffs: "
             f"{report['eligible_rows']:,}. Data after {report['reserved_after'][:10]} is reserved and unused. "
             f"{report['historical_test_status']}.", '',
             '## Direction (probability price is higher at the horizon)', '',
             '| Group | Horizon | Training | Folds | Brier | Constant | Folds beating constant | AUC | Pinball / constant | 80% interval coverage |',
             '|---|---|---|---|---|---|---|---|---|---|']
    for r in s['direction']:
        auc = '—' if r['mean_auc'] is None else f"{r['mean_auc']:.3f}"
        lines.append(f"| {r['group']} | {r['horizon']} | {r['training']} | {r['folds']} | {r['mean_brier']:.4f} | "
                     f"{r['mean_constant_brier']:.4f} | {r['folds_beating_constant']}/{r['folds']} | {auc} | "
                     f"{r['mean_pinball']:.5f} / {r['mean_constant_pinball']:.5f} | {r['mean_interval_coverage_80']*100:.0f}% |")
    lines += ['', '## Champion per horizon (selected on validation only)', '',
              '| Horizon | Champion | Validation skill | Test skill | Test folds beating constant | Configurations tried | PBO |',
              '|---|---|---|---|---|---|---|']
    for horizon, c in s['champions'].items():
        pbo = s['backtest_overfitting'].get(horizon, {}).get('pbo')
        name = c['group'] if c['training'] is None else f"{c['group']} / {c['training']}"
        test = '—' if c['test_skill'] is None else f"{c['test_skill']:+.5f}"
        beat = '—' if c['folds_beating_constant'] is None else f"{c['folds_beating_constant']}/{c.get('folds', 0)}"
        lines.append(f"| {horizon} | {name} | {c['validation_skill']:+.5f} | {test} | {beat} | "
                     f"{c['configurations_tried']} | {'—' if pbo is None else f'{pbo:.2f}'} |")
    lines += ['', 'Skill is the constant base-rate Brier minus the model Brier; positive means better than always '
              'predicting the training up-rate. PBO near 0 means validation picks also do well out of sample; '
              'near 1 means the selection is overfitting.']
    d = s['drop_risk']
    drop_auc = '—' if d['mean_auc'] is None else f"{d['mean_auc']:.3f}"
    lines += ['', '## Large-drop risk (touch -5% within 24h)', '',
              f"Evaluated windows: {d['evaluated']} (skipped {d['skipped']}); beat the constant base rate in "
              f"{d['beats_constant']}; mean AUC {drop_auc}.",
              '', '## Volatility: EWMA vs GARCH(1,1) (QLIKE, lower is better)', '',
              '| Horizon | Windows | GARCH better | Mean QLIKE EWMA | Mean QLIKE GARCH |', '|---|---|---|---|---|']
    for v in s['volatility']:
        lines.append(f"| {v['horizon']} | {v['windows']} | {v['garch_wins']} | {v['mean_qlike_ewma']:.4f} | {v['mean_qlike_garch']:.4f} |")
    lines += ['', '## Not yet done', ''] + [f'- {item}' for item in report['incomplete']]
    return '\n'.join(lines)+'\n'


def drop_scores(x, target, windows, seed):
    """Calibrated large-drop classifier scored on validation and test against the training base rate."""
    tr, ca, va, te = (windows[k] for k in ('train', 'calibration', 'validation', 'test'))
    if target.loc[tr].sum() < 20 or target.loc[ca].nunique() < 2:
        return dict(status='skipped', reason='too few large drops to fit and calibrate',
                    train_positives=int(target.loc[tr].sum()))
    model = XGBClassifier(n_estimators=80, max_depth=3, learning_rate=.05, reg_lambda=8, min_child_weight=50,
                          subsample=.8, colsample_bytree=.8, n_jobs=2, random_state=seed, eval_metric='logloss')
    model.fit(x.loc[tr], target.loc[tr])
    model = CalibratedClassifierCV(FrozenEstimator(model), method='sigmoid').fit(x.loc[ca], target.loc[ca])
    rate = float(target.loc[tr].mean())
    out = dict(status='evaluated', base_rate_train=rate)
    for name, idx in (('validation_', va), ('', te)):
        actual, p = target.loc[idx].to_numpy(), model.predict_proba(x.loc[idx])[:, 1]
        out[f'{name}brier'] = brier(actual, p)
        out[f'{name}constant_brier'] = brier(actual, np.repeat(rate, len(actual)))
        out[f'{name}auc'] = float(roc_auc_score(actual, p)) if len(set(actual)) > 1 else None
    return out


def risk_experiment(output, folds=4, frames=None, runs=None, macro_events=None, min_days=90,
                    groups=('price', 'technical', 'derivatives', 'full', 'macro'), horizons=tuple(HORIZONS),
                    reserve_months=2, seed=42, train_step=3):
    """Return ranges, large-drop risk and volatility, each scored on validation AND test on the same folds,
    so their champions can be chosen on validation only. Research only; resumable like train_experiment."""
    output = Path(output)
    config = dict(kind='risk', folds=folds, min_days=min_days, groups=list(groups), horizons=list(horizons),
                  reserve_months=reserve_months, seed=seed, train_step=train_step,
                  code=sha256(Path(__file__).read_bytes()).hexdigest())
    done, record = _progress(output, config)
    supplied = frames is not None
    data = _prepare(frames, [] if runs is None else runs, macro_events, groups, horizons, min_days,
                    reserve_months, folds)
    f, y, d = data['f'], data['y'], data['d']
    ranges, drops, vols = [], [], []
    def keep(rows, row):
        rows.append(row)
        record(row)
    for fold, test_start in enumerate(data['test_starts']):
        for training, lookback in (('expanding', None), ('rolling90d', 90)):
            windows = split(data['common'], test_start, rolling_days=lookback)
            if min(map(len, windows.values())) < 288:
                continue
            windows = thin(windows, train_step)
            tr, va, te = windows['train'], windows['validation'], windows['test']
            span = {k: [v.min().isoformat(), v.max().isoformat()] for k, v in windows.items()}
            if training == 'expanding':
                for horizon in horizons:
                    key = f'{fold}-vol-{horizon}'
                    if key in done:
                        vols.append(done[key])
                        continue
                    val = volatility_scores(d.close, tr, va, HORIZONS[horizon])
                    test = volatility_scores(d.close, tr, te, HORIZONS[horizon], fitted=val['garch'])
                    keep(vols, dict(id=key, fold=fold, horizon=horizon, windows=span, garch=val['garch'],
                                    **{f'validation_{k}': v for k, v in val.items() if k != 'garch'},
                                    **{k: v for k, v in test.items() if k != 'garch'}))
            for group in groups:
                x = f[data['group_columns'][group]]
                for horizon in horizons:
                    key = f'{fold}-{training}-{group}-{horizon}-range'
                    if key in done:
                        ranges.append(done[key])
                        continue
                    returns = y['return_'+horizon]
                    model = quantile_model(seed).fit(x.loc[tr], returns.loc[tr])
                    base = np.quantile(returns.loc[tr], QUANTILES)
                    val, test = range_scores(model, x, returns, va, base), range_scores(model, x, returns, te, base)
                    keep(ranges, dict(id=key, fold=fold, training=training, group=group, horizon=horizon, windows=span,
                                      **{f'validation_{k}': v for k, v in val.items()}, **test))
                    print(json.dumps({'completed': key, 'pinball': test['pinball'], 'constant': test['constant_pinball']}),
                          flush=True)
                key = f'{fold}-{training}-{group}-drop'
                if key in done:
                    drops.append(done[key])
                    continue
                keep(drops, dict(id=key, fold=fold, training=training, group=group, horizon='24h', windows=span,
                                 **drop_scores(x, y['drop5_24h'], windows, seed)))
    evaluated = [r for r in drops if r.get('status') == 'evaluated']
    summary = dict(range_champions=champions(ranges, metric='pinball'),
                   range_overfitting=backtest_overfitting(ranges, metric='pinball'),
                   drop_champion=champions(evaluated).get('24h', dict(group='constant', training=None,
                       note='No drop-risk configuration could be evaluated')),
                   drop_overfitting=backtest_overfitting(evaluated).get('24h'),
                   volatility_champions=volatility_champions(vols))
    report = dict(status='research_only', promotion_allowed=False, seed=seed, purge_hours=24, train_step=train_step,
                  data_hashes='supplied_frames' if supplied else dataset_hashes(settings.data_dir),
                  code_hash=config['code'], eligible_rows=len(data['common']), reserved_after=data['end'].isoformat(),
                  historical_test_status='Previously inspected data; not an untouched holdout',
                  resumed_results=len(done), ranges=ranges, drops=drops, volatility=vols, summary=summary)
    (output/'evaluation.json').write_text(json.dumps(report, indent=2, allow_nan=False, default=str), encoding='utf-8')
    (output/'report.md').write_text(render_risk(report), encoding='utf-8')
    return report


def render_risk(report):
    s = report['summary']
    lines = ['# Risk forecast experiment: ranges, large-drop risk and volatility', '',
             f"Research only. Eligible cutoffs: {report['eligible_rows']:,}; data after {report['reserved_after'][:10]} "
             f"reserved. {report['historical_test_status']}. Every champion below is chosen on validation only.", '',
             '## Return ranges (10/50/90% quantiles; skill = constant pinball minus model pinball)', '',
             '| Horizon | Champion | Validation skill | Test skill | Test folds better | Configurations | PBO |',
             '|---|---|---|---|---|---|---|']
    for horizon, c in s['range_champions'].items():
        pbo = s['range_overfitting'].get(horizon, {}).get('pbo')
        name = c['group'] if c['training'] is None else f"{c['group']} / {c['training']}"
        test = '—' if c['test_skill'] is None else f"{c['test_skill']:+.6f}"
        beat = '—' if c['folds_beating_constant'] is None else f"{c['folds_beating_constant']}/{c.get('folds', 0)}"
        lines.append(f"| {horizon} | {name} | {c['validation_skill']:+.6f} | {test} | {beat} | "
                     f"{c['configurations_tried']} | {'—' if pbo is None else f'{pbo:.2f}'} |")
    c = s['drop_champion']
    pbo = (s.get('drop_overfitting') or {}).get('pbo')
    lines += ['', '## Large-drop risk (touch -5% within 24h; skill = constant Brier minus model Brier)', '',
              f"Champion: **{c['group'] if c.get('training') is None else c['group']+' / '+c['training']}** · "
              f"validation skill {c.get('validation_skill', 0):+.6f} · test skill "
              f"{'—' if c.get('test_skill') is None else format(c['test_skill'], '+.6f')} · PBO "
              f"{'—' if pbo is None else f'{pbo:.2f}'}", '',
              '## Volatility (QLIKE, lower is better)', '',
              '| Horizon | Champion | Validation QLIKE | Test QLIKE | Alternative test QLIKE | Test windows better |',
              '|---|---|---|---|---|---|']
    for horizon, v in s['volatility_champions'].items():
        lines.append(f"| {horizon} | {v['model']} | {v['validation_qlike']:.4f} | {v['test_qlike']:.4f} | "
                     f"{v['test_qlike_alternative']:.4f} | {v['test_windows_better']}/{v['windows']} |")
    return '\n'.join(lines)+'\n'
