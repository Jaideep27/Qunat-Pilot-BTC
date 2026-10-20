"""'Similar situations' estimator: past hours whose market state resembled now, and what followed.

State values are converted to percentile ranks among their own past (expanding window), so no
future distribution leaks in. Estimates shrink toward the base rate; a walk-forward gate decides
whether the estimate may set a headline probability.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STATE = ['realized_vol_24h', 'ret_24h', 'ema_50_distance', 'rsi_14', 'funding_rate', 'oi_change_24h',
         'global_ratio', 'hours_to_next_macro']


def with_macro_lead(panel):
    out = panel.copy()
    leads = [c for c in ('hours_to_next_cpi', 'hours_to_next_jobs', 'hours_to_next_fomc') if c in out]
    out['hours_to_next_macro'] = out[leads].min(axis=1) if leads else np.nan
    return out


def state_ranks(panel):
    frame = panel if 'hours_to_next_macro' in panel else with_macro_lead(panel)
    return pd.DataFrame({c: frame[c].expanding().rank(pct=True) for c in STATE}, index=frame.index)


def forward_up(panel, h):
    future = panel.close.shift(-h)
    return (future > panel.close).astype(float).where(future.notna())


def wilson(hits, n, z=1.96):
    if not n:
        return [None, None]
    p = hits/n
    centre, margin = p+z*z/(2*n), z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return [float((centre-margin)/(1+z*z/n)), float((centre+margin)/(1+z*z/n))]


def estimate(ranks, outcome, t, h, k=200, m=100):
    """Nearest past states (one per UTC day) whose h-hour outcome was known by t; shrunk to the base rate."""
    t = pd.Timestamp(t)
    now = ranks.loc[t]
    finished = outcome.notna() & (outcome.index+pd.Timedelta(hours=h) <= t)
    base = float(outcome[finished].mean()) if finished.any() else None
    missing = [c for c in now.index if pd.isna(now[c])]
    known = finished & ranks.notna().all(axis=1)
    if missing or not known.any():
        return dict(probability=None, base_rate=base, hits=0, k_eff=0, ci=[None, None], missing=missing)
    past = ranks[known]
    results = outcome[known]
    distance = np.sqrt(((past.to_numpy()-now.to_numpy())**2).sum(axis=1))
    order = np.argsort(distance, kind='stable')
    # At most one neighbour per UTC day: keep each day's nearest hour, then the k nearest of those.
    _, first = np.unique(past.index[order].floor('D').asi8, return_index=True)
    picked = order[first]
    picked = picked[np.argsort(distance[picked], kind='stable')][:k]
    hits = float(results.iloc[picked].sum())
    k_eff = len(picked)
    probability = (hits+m*base)/(k_eff+m) if k_eff+m else None
    return dict(probability=probability, base_rate=base, hits=hits, k_eff=k_eff, ci=wilson(hits, k_eff), missing=[])


def promote(result):
    yearly = result.get('yearly') or []
    return bool(result.get('skill_validation', 0) > 0 and result.get('skill_confirmation', 0) > 0
                and yearly and 2*sum(s > 0 for s in yearly) >= len(yearly)+1)


def validate(panel, horizons=(1, 4, 12, 24, 72, 168), step_hours=12, burn_in_days=365, confirm_days=365):
    """Walk-forward Brier skill of the analog estimate vs the past base rate at sampled cutoffs."""
    ranks = state_ranks(panel)
    start = panel.index.min()+pd.Timedelta(days=burn_in_days)
    report = {}
    for h in horizons:
        outcome = forward_up(panel, h)
        cutoffs = [t for t in panel.index[panel.index >= start][::step_hours] if pd.notna(outcome.get(t))]
        rows = []
        for t in cutoffs:
            result = estimate(ranks, outcome, t, h)
            if result['probability'] is None:
                continue
            y = outcome.loc[t]
            rows.append((t, (y-result['probability'])**2, (y-result['base_rate'])**2, result['probability'], y))
        frame = pd.DataFrame(rows, columns=['t', 'model', 'base', 'p', 'y']).set_index('t')
        if frame.empty:
            report[f'{h}h'] = dict(windows=0, promoted=False)
            continue
        split = frame.index.max()-pd.Timedelta(days=confirm_days)
        val, conf = frame[frame.index <= split], frame[frame.index > split]
        skill = lambda part: float((part.base-part.model).mean()) if len(part) else 0.0
        yearly = [skill(g) for _, g in val.groupby(val.index.year) if len(g) >= 20]
        buckets = pd.cut(frame.p, [0, .4, .45, .5, .55, .6, 1])
        calibration = [dict(bucket=str(b), predicted=float(g.p.mean()), observed=float(g.y.mean()), n=int(len(g)))
                       for b, g in frame.groupby(buckets, observed=True)]
        result = dict(windows=int(len(frame)), skill_validation=skill(val), skill_confirmation=skill(conf),
                      yearly=yearly, relative_improvement=float(skill(conf)/conf.base.mean()) if len(conf) else 0.0,
                      calibration=calibration)
        result['promoted'] = promote(result)
        report[f'{h}h'] = result
    return report
