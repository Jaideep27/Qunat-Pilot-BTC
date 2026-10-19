"""Causal event features and multi-horizon research. Never publishes a trading model."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import brier_score_loss, mean_pinball_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .data import load_frames, dataset_hashes
from .features import build_features, feature_groups
from .news import NewsRun, snapshot
from .store import database

HORIZONS = {'1h': 12, '4h': 48, '24h': 288}
PURGE = pd.Timedelta(hours=24)


def news_features(index, runs):
    """Rolling evidence available AFTER actual reasoning completion.

    Unknown before the first successful forward extraction; no zero-filled
    historical sentiment. Results backfilled by today's LLM are excluded.
    """
    records = []
    for run in runs:
        if run['status'] != 'completed' or run.get('retrospective', True):
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


def targets(close):
    if close.index.tz is None or close.index.has_duplicates or not close.index.is_monotonic_increasing:
        raise ValueError('unique ordered timezone-aware cutoffs required')
    if (close.index.to_series().diff().dropna() != pd.Timedelta(minutes=5)).any():
        raise ValueError('targets require contiguous five-minute cutoffs')
    result = pd.DataFrame(index=close.index)
    for label, bars in HORIZONS.items():
        forward = close.shift(-bars)/close-1
        result[f'return_{label}'] = forward
        result[f'up_{label}'] = (forward > 0).astype(float).where(forward.notna())
    # Endpoint drawdown target is distinct from touching -5% intrahorizon.
    r = result['return_24h']
    result['drop5_24h'] = (r <= -.05).astype(float).where(r.notna())
    return result


def episodes(data, features, threshold=.05):
    daily = data.close/data.close.shift(288)-1
    chosen = []
    last = {'drop': None, 'rally': None}
    for t, value in daily.dropna().items():
        kind = 'drop' if value <= -threshold else 'rally' if value >= threshold else None
        if kind is None or (last[kind] is not None and t-last[kind] < pd.Timedelta(hours=24)):
            continue
        last[kind] = t
        fields = ['rsi_14', 'relative_volume', 'taker_imbalance', 'oi_change', 'funding_rate',
                  'global_ratio', 'top_account_ratio', 'top_position_ratio', 'atr_fraction']
        state = features.loc[t, [c for c in fields if c in features]].replace([np.inf, -np.inf], np.nan)
        chosen.append(dict(time=t.isoformat(), kind=kind, observed_return_24h=float(value),
                           context={c: None if pd.isna(v) else float(v) for c, v in state.items()},
                           interpretation='Observed association; no causal attribution'))
    return chosen


def split(index, test_start, test_days=14, rolling_days=None):
    start = pd.Timestamp(test_start)
    validation_start = start-pd.Timedelta(days=14)
    calibration_start = validation_start-pd.Timedelta(days=14)
    lower = index.min() if rolling_days is None else calibration_start-pd.Timedelta(days=rolling_days)
    return dict(train=index[(index >= lower) & (index < calibration_start-PURGE)],
                calibration=index[(index >= calibration_start) & (index < validation_start-PURGE)],
                validation=index[(index >= validation_start) & (index < start-PURGE)],
                test=index[(index >= start) & (index < start+pd.Timedelta(days=test_days)-PURGE)])


def brier(y, p):
    return float(brier_score_loss(y, p))


def train_experiment(output, folds=2):
    """New research directory only. Historical tests are explicitly previously observed.

    This implements mathematical/ML benchmarks and a soft validation-weighted
    regime ensemble. No holdout is relabeled 'untouched' and no release is made.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    frames = load_frames(settings.data_dir)
    f, _, d = build_features(frames['candles'], frames)
    y = targets(d.close)
    with Session(database()) as session:
        runs = [dict(status=r.status, **r.payload) for r in session.scalars(select(NewsRun)).all()]
    nf = news_features(f.index, runs)
    f = f.join(nf)
    base_columns = [c for c in f if not c.startswith('news_')]
    groups = feature_groups(base_columns)
    common = f[base_columns].dropna().index.intersection(y.dropna().index)
    if len(common) < 288*90:
        raise ValueError('At least 90 days of common eligible history required')
    # Preserve the last two months for forward-design discipline, while declaring all
    # historical data previously inspected. This is not a new untouched holdout.
    end = common.max()-pd.DateOffset(months=2)
    test_starts = [end-pd.Timedelta(days=14*i) for i in reversed(range(folds))]
    rows, artifacts = [], {}
    seed = 42
    for fold, test_start in enumerate(test_starts):
        for strategy, lookback in (('expanding', None), ('rolling90d', 90)):
            windows = split(common, test_start, rolling_days=lookback)
            if min(map(len, windows.values())) < 288:
                continue
            for group in ('price', 'technical', 'derivatives', 'full'):
                columns = groups[group]
                tr, ca, va, te = [windows[k] for k in ('train', 'calibration', 'validation', 'test')]
                x = f[columns]
                # Fixed causal condition flag. Weights are fitted on validation only.
                regime = (f['regime'].fillna(0) > .5).astype(int)
                for horizon in HORIZONS:
                    target = y['up_'+horizon]
                    if min(target.loc[tr].nunique(), target.loc[ca].nunique()) < 2:
                        continue
                    models = [make_pipeline(StandardScaler(), LogisticRegression(C=.1, max_iter=500)),
                              XGBClassifier(n_estimators=60, max_depth=3, learning_rate=.05,
                                            reg_lambda=8, n_jobs=2, random_state=seed, eval_metric='logloss')]
                    calibrated, vp, tp = [], [], []
                    for model in models:
                        model.fit(x.loc[tr], target.loc[tr])
                        model = CalibratedClassifierCV(FrozenEstimator(model), method='sigmoid')
                        model.fit(x.loc[ca], target.loc[ca])
                        calibrated.append(model)
                        vp.append(model.predict_proba(x.loc[va])[:, 1])
                        tp.append(model.predict_proba(x.loc[te])[:, 1])
                    weights = {}
                    combined = np.zeros(len(te))
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
                    rtrain, rtest = y.loc[tr, 'return_'+horizon], y.loc[te, 'return_'+horizon]
                    ridge = make_pipeline(StandardScaler(), Ridge(alpha=100.))
                    ridge.fit(x.loc[tr], rtrain)
                    expected = ridge.predict(x.loc[te])
                    # EWMA variance is causal; horizon scaling is an explicit approximation.
                    sigma = d.close.pct_change().ewm(span=288, adjust=False).std().loc[te]
                    sigma = sigma*np.sqrt(HORIZONS[horizon])
                    key = f'{fold}-{strategy}-{group}-{horizon}'
                    row = dict(id=key, fold=fold, training=strategy, group=group, horizon=horizon,
                        train_rows=len(tr), test_rows=len(te),
                        windows={k: [v.min().isoformat(), v.max().isoformat()] for k, v in windows.items()},
                        weights=weights, brier=brier(target.loc[te], combined),
                        constant_brier=brier(target.loc[te], np.repeat(target.loc[tr].mean(), len(te))),
                        auc=float(roc_auc_score(target.loc[te], combined)) if target.loc[te].nunique() > 1 else None,
                        return_mae=float(np.mean(np.abs(expected-rtest))),
                        zero_return_mae=float(np.mean(np.abs(rtest))),
                        ewma_normal90_coverage=float((np.abs(rtest) <= 1.645*sigma).mean()))
                    artifacts[key] = dict(models=calibrated, weights=weights, return_model=ridge, columns=columns)
                    rows.append(row)
                    print(json.dumps({'completed': key, 'brier': row['brier']}), flush=True)
    report = dict(status='research_only', promotion_allowed=False, seed=seed, purge_hours=24,
        data_hashes=dataset_hashes(settings.data_dir),
        code_hash=sha256(Path(__file__).read_bytes()).hexdigest(),
        news_eligible_rows=int(nf.dropna().shape[0]),
        news_experiment_status='blocked_until_forward_news_history_and_agent_validation',
        historical_test_status='Previously inspected data; not an untouched holdout',
        incomplete=['news and macro ablations', 'quantile and tail models', 'cost-aware shared-engine replay',
                    'independent uncertainty intervals', 'fresh forward validation'],
        comparisons=rows, episodes=episodes(d, f),
        news_coverage=snapshot()['scope'])
    (output/'evaluation.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    joblib.dump(artifacts, output/'candidates.joblib')
    return report
