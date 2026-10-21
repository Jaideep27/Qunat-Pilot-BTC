"""M3: the ML filter (meta-labeling) over playbook hits, exactly as registered in amendment A6.

The playbook decides the side and the plan; this model only decides whether a confirmed hit is worth
taking: L2 logistic regression on standardized features, one per playbook (pooled by family when a
playbook has fewer than 200 training hits), refit at each 6-month walk-forward block on hits that exited
before the block. A hit is kept when p > 1/(1+RR), the break-even win probability, so no threshold is
tuned. The filter goes live for a playbook only if its walk-forward test trades number at least 30 and
their mean net return beats the unfiltered mean with a bootstrap 95% interval above zero.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .config import settings
from .playbook_research import ML_FEATURES, TRIGGER_FACTS, load_hits, playbook_trades
from .strategy_research import blocks

REGIMES = ('RANGE', 'TREND_UP', 'TREND_DOWN', 'HIGH_VOL', 'PANIC')
FEATURES = ([f'f_{c}' for c in ML_FEATURES]+[f'tf_{c}' for c in TRIGGER_FACTS]
            + ['confluence', 'plan_reward_risk', 'plan_risk_fraction', 'is_long']+[f'regime_{r}' for r in REGIMES])
MIN_TRAIN, MIN_TEST, BOOTSTRAP = 200, 30, 2000
MODELS = settings.artifact_dir/'rule-engine'/'meta-models.joblib'
MODES = settings.artifact_dir/'rule-engine'/'modes.json'


def matrix(frame):
    out = pd.DataFrame(index=frame.index)
    for c in FEATURES:
        if c == 'is_long':
            out[c] = (frame['side'] == 'long').astype(float)
        elif c.startswith('regime_'):
            out[c] = (frame['regime'] == c[7:]).astype(float)
        else:
            out[c] = pd.to_numeric(frame[c], errors='coerce') if c in frame else np.nan
    return out.astype(float)


def _usable(frame):
    return frame[frame.tradeable & (frame.o_valid == True) & frame.o_net.notna()]  # noqa: E712


def fit(frame, until):
    """A model on hits that exited by `until`; None if fewer than MIN_TRAIN or a single class."""
    until = pd.Timestamp(until)
    train = _usable(frame)
    train = train[pd.to_datetime(train.o_exit_time, utc=True) <= until]
    y = (train.o_net > 0).astype(int)
    if len(train) < MIN_TRAIN or y.nunique() < 2:
        return None
    x = matrix(train)
    x = x.loc[:, x.notna().any()]   # columns with no data at all carry nothing
    model = make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    model.fit(x, y)
    return dict(model=model, columns=list(x.columns), n=int(len(train)), trained_until=str(until.date()))


def predict(model, frame):
    return model['model'].predict_proba(matrix(frame)[model['columns']])[:, 1]


def _scoped(frame, playbook, family, until):
    own = frame[frame.playbook == playbook]
    model = fit(own, until)
    if model is not None:
        return model, 'playbook'
    model = fit(frame[frame.family == family], until)
    return (model, 'family') if model is not None else (None, None)


def _bootstrap(a, b, reps=BOOTSTRAP, seed=0):
    """Percentile 95% interval of mean(a) - mean(b), resampling each set."""
    rng = np.random.default_rng(seed)
    a, b = np.asarray(a, float), np.asarray(b, float)
    diffs = rng.choice(a, (reps, len(a))).mean(axis=1)-rng.choice(b, (reps, len(b))).mean(axis=1)
    return float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))


def evaluate(book, hits):
    """Walk-forward ablation per playbook: unfiltered vs ML-filtered trades on the same test blocks."""
    hits = hits.copy()
    hits['time'] = pd.to_datetime(hits.time, utc=True)
    usable = _usable(hits)
    out = {}
    if usable.empty:
        return dict(playbooks={}, live=[])
    block_list = blocks(usable.time.min())
    for pid, group in usable.groupby('playbook'):
        family = group.family.iloc[0]
        tested, scopes, models = [], [], []
        for start, end in block_list:
            test = group[(group.time >= start) & (group.time < end)]
            if test.empty:
                continue
            model, scope = _scoped(usable, pid, family, start)
            keep = np.ones(len(test), bool) if model is None else \
                predict(model, test) > 1/(1+test.plan_reward_risk.clip(lower=1e-6).to_numpy())
            tested.append(test.assign(ml_keep=keep, ml_model=model is not None))
            scopes.append(scope)
            models.append(None if model is None else dict(block=str(start.date()), n=model['n'], scope=scope))
        if not tested:
            continue
        tested = pd.concat(tested)
        base = playbook_trades(book, tested, pid)
        filtered = playbook_trades(book, tested.assign(tradeable=tested.tradeable & tested.ml_keep), pid)
        report = lambda trades: dict(calls=len(trades), mean_net=float(np.mean([t['net'] for t in trades])) if trades else None,
                                     hit_rate=float(np.mean([t['net'] > 0 for t in trades])) if trades else None)
        diff = None
        if len(filtered) >= 2 and len(base) >= 2:
            low, high = _bootstrap([t['net'] for t in filtered], [t['net'] for t in base])
            diff = dict(mean=report(filtered)['mean_net']-report(base)['mean_net'], low=low, high=high)
        live = bool(len(filtered) >= MIN_TEST and diff is not None and diff['low'] > 0 and any(tested.ml_model))
        used = [s for s in scopes if s]
        out[pid] = dict(family=family, scope=max(set(used), key=used.count) if used else None, models=[m for m in models if m],
                        unfiltered=report(base), filtered=report(filtered), kept_share=float(tested.ml_keep.mean()),
                        difference=diff, live=live)
    return dict(playbooks=out, live=sorted(p for p, r in out.items() if r['live']))


def train_final(hits, path=MODELS):
    """Final models on every development hit, per playbook (>= 200 hits) and per family, for the live filter."""
    hits = hits.copy()
    usable = _usable(hits)
    end = pd.to_datetime(usable.o_exit_time, utc=True).max() if len(usable) else pd.Timestamp.now(tz='UTC')
    models = {}
    for pid, group in usable.groupby('playbook'):
        model = fit(group, end)
        if model is not None:
            models[('playbook', pid)] = model
    for family, group in usable.groupby('family'):
        model = fit(group, end)
        if model is not None:
            models[('family', family)] = model
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(models, path)
    return path


def _row(hit, features):
    plan = hit.get('plan') or {}
    facts = (hit.get('trigger') or {}).get('facts', {})
    row = {f'f_{c}': features.get(c) for c in ML_FEATURES} | {f'tf_{c}': facts.get(c) for c in TRIGGER_FACTS}
    row |= dict(confluence=(hit.get('confluence') or {}).get('score'), plan_reward_risk=plan.get('reward_risk'),
                plan_risk_fraction=plan.get('risk_fraction'), side=hit.get('side', 'long'), regime=hit.get('regime'))
    return pd.DataFrame([row])


def live_filter():
    """`(hit, features) -> verdict` from the final models, or None when no models exist."""
    if not Path(MODELS).exists():
        return None
    models = joblib.load(MODELS)
    def verdict(hit, features):
        model = models.get(('playbook', hit['playbook'])) or models.get(('family', hit.get('family')))
        rr = (hit.get('plan') or {}).get('reward_risk')
        if model is None or not rr:
            return dict(verdict='none')
        p = float(predict(model, _row(hit, features))[0])
        return dict(verdict='take' if p > 1/(1+rr) else 'skip', p=p, threshold=1/(1+rr), trained_until=model['trained_until'])
    return verdict


def write_modes(layer, live, path=MODES):
    path = Path(path)
    stored = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    stored[layer] = {pid: 'live' for pid in live}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stored, indent=2), encoding='utf-8')
    return stored


def run(output, book=None):
    """Evaluate the ML filter on a research run's hits; write meta-report.json, the live modes and final models."""
    from . import rules
    book = book or rules.load()
    hits = load_hits(output)
    result = evaluate(book, hits)
    live = result['live']
    result['summary'] = (f"live for {', '.join(live)}" if live else 'kept in shadow for every playbook')
    result['generated'] = pd.Timestamp.now(tz='UTC').isoformat()
    (Path(output)/'meta-report.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    write_modes('ml', live)
    train_final(hits)
    return result
