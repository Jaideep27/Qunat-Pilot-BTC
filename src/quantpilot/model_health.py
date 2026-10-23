"""Model health (A9): is the live A8 score model still right, has the market moved away from what it learned,
and are its inputs clean?

The reference file holds what the model learned from: per input its present share, range, quintile edges and
training shares; the critical inputs (present in at least 99% of training setups); the development calibration;
and the drift baseline, i.e. how unusual ordinary 50-setup stretches of the development setups looked. Live
setups are compared against it. Monitoring never changes the model or the accounts; only the data check holds a
setup with bad inputs back.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import settings

CRITICAL = .99
EPS = 1e-4
WINDOW, STEP = 50, 10
MIN_CALIBRATION = 30
BINS = ((0, .35), (.35, .5), (.5, 1.01))


def reference_path():
    return settings.artifact_dir/'rule-engine'/'accounts-score-reference.json'


def _shares(values, edges):
    v = pd.to_numeric(pd.Series(values), errors='coerce').to_numpy(float)
    missing = np.isnan(v)
    bins = np.searchsorted(np.asarray(edges, float), v[~missing], side='right')
    counts = np.bincount(bins, minlength=len(edges)+1).astype(float)
    counts = np.append(counts, missing.sum())
    return counts/max(len(v), 1)


def psi(values, spec):
    """Population stability index of `values` against one input's training bins (a missing bin of its own)."""
    live, train = _shares(values, spec['edges'])+EPS, np.asarray(spec['shares'], float)+EPS
    return float(np.sum((live-train)*np.log(live/train)))


def _spec(column):
    present = column.dropna()
    edges = list(np.quantile(present, [.2, .4, .6, .8])) if len(present) else [0.0]*4
    return dict(present=float(column.notna().mean()), min=float(present.min()) if len(present) else None,
                max=float(present.max()) if len(present) else None, median=float(present.median()) if len(present) else None,
                edges=[float(e) for e in edges], shares=[float(s) for s in _shares(column, edges)])


def _bad(x, ref):
    """Boolean frame-wide check (one flag per row), used for the training block rate."""
    out = pd.Series(False, index=x.index)
    for col, spec in ref['inputs'].items():
        v = pd.to_numeric(x[col], errors='coerce') if col in x else pd.Series(np.nan, index=x.index)
        if col in ref['critical']:
            out |= v.isna()
        out |= np.isinf(v)
        if spec['min'] is not None:
            w = spec['max']-spec['min']
            out |= (v < spec['min']-w) | (v > spec['max']+w)
    return out


def _counts(rows, ref, thresholds):
    return sum(psi(rows[c], ref['inputs'][c]) > thresholds[c] for c in thresholds)


def build_reference(x, model_hash, wf=None, window=WINDOW, step=STEP):
    """Reference statistics from the training inputs `x` (model columns, time order) and walk-forward `p`/`o_net`."""
    x = x.astype(float)
    ref = dict(model_hash=model_hash, rows=int(len(x)), inputs={c: _spec(x[c]) for c in x.columns})
    ref['critical'] = [c for c, s in ref['inputs'].items() if s['present'] >= CRITICAL]
    ref['blocked_share'] = float(_bad(x, ref).mean())
    if wf is not None and len(wf):
        won = (wf.o_net > 0).astype(float)
        ref['brier'] = float(np.mean((wf.p-won)**2))
        ref['calibration'] = [dict(n=int(m.sum()), predicted=float(wf.p[m].mean()), actual=float(won[m].mean()))
                              for lo, hi in BINS if (m := (wf.p >= lo) & (wf.p < hi)).any()]
    starts = range(0, len(x)-window+1, step)
    table = pd.DataFrame([{c: psi(x[c].iloc[i:i+window], ref['inputs'][c]) for c in x.columns} for i in starts])
    thresholds = {c: float(table[c].quantile(.95)) for c in x.columns}
    counts = (table > pd.Series(thresholds)).sum(axis=1)
    ref['drift_baseline'] = dict(window=window, step=step, windows=int(len(table)), thresholds=thresholds,
                                 counts_p90=float(counts.quantile(.9)), counts_p99=float(counts.quantile(.99)))
    return ref


def load_reference(model_hash, path=None):
    """The reference for this model, or None when the file is missing, unreadable or for another model."""
    try:
        ref = json.loads(Path(path or reference_path()).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return ref if ref.get('model_hash') == model_hash else None


def data_check(x, ref):
    """Reasons a setup's model inputs are not trustworthy; empty when they pass."""
    reasons = []
    for col, spec in ref['inputs'].items():
        try:
            v = float(x.get(col)) if x.get(col) is not None else np.nan
        except (TypeError, ValueError):
            v = np.nan
        if np.isnan(v):
            if col in ref['critical']:
                reasons.append(f'missing:{col}')
        elif np.isinf(v):
            reasons.append(f'inf:{col}')
        elif spec['min'] is not None and not (2*spec['min']-spec['max'] <= v <= 2*spec['max']-spec['min']):
            reasons.append(f'range:{col}')
    return reasons


def wilson(k, n, z=1.96):
    if not n:
        return None, None
    p = k/n
    centre, half = p+z*z/(2*n), z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return float((centre-half)/(1+z*z/n)), float((centre+half)/(1+z*z/n))


def calibration(pairs, ref):
    """Predicted win chance against real outcomes. `pairs`: one row per resolved setup with `p` and `won`."""
    p, won = pairs.p.astype(float), pairs.won.astype(bool)
    n, k = int(len(pairs)), int(won.sum())
    lo, hi = wilson(k, n)
    predicted = float(p.mean()) if n else None
    if n < MIN_CALIBRATION:
        verdict = 'TOO_FEW'
    elif predicted > hi:
        verdict = 'OVERCONFIDENT'
    elif predicted < lo:
        verdict = 'UNDERCONFIDENT'
    else:
        verdict = 'IN_LINE'
    bins = [dict(range=[a, min(b, 1.0)], n=int(m.sum()), predicted=float(p[m].mean()) if m.any() else None,
                 actual=float(won[m].mean()) if m.any() else None) for a, b in BINS for m in [(p >= a) & (p < b)]]
    return dict(n=n, wins=k, predicted=predicted, actual=k/n if n else None, lo=lo, hi=hi,
                brier=float(np.mean((p-won)**2)) if n else None, dev_brier=(ref or {}).get('brier'), bins=bins,
                verdict=verdict)


def label(col):
    if col.startswith('tf_'):
        return 'trigger: '+col[3:].replace('_', ' ')
    if col.startswith('regime_'):
        return 'regime '+col[7:].lower().replace('_', ' ')
    return (col[2:] if col.startswith('f_') else col).replace('_', ' ')


def drift(rows, ref):
    """How unusual the latest live setups' inputs look, judged against ordinary development stretches."""
    base = ref['drift_baseline']
    rows = rows.tail(base['window'])
    n = int(len(rows))
    if n < base['window']:
        return dict(n=n, window=base['window'], count=None, verdict='TOO_FEW', top=[])
    scores = {c: psi(rows[c] if c in rows else pd.Series(np.nan, index=rows.index), ref['inputs'][c])
              for c in base['thresholds']}
    over = {c: s for c, s in scores.items() if s > base['thresholds'][c]}
    count = len(over)
    verdict = 'ALARM' if count > base['counts_p99'] else 'WATCH' if count > base['counts_p90'] else 'STABLE'
    top = sorted(scores, key=lambda c: scores[c]-base['thresholds'][c], reverse=True)[:5]
    live = rows.apply(pd.to_numeric, errors='coerce')
    return dict(n=n, window=base['window'], count=count, usual=base['counts_p90'], verdict=verdict,
                top=[dict(input=c, label=label(c), psi=scores[c], usual=base['thresholds'][c],
                          live_median=float(live[c].median()) if c in live and live[c].notna().any() else None,
                          train_median=ref['inputs'][c]['median']) for c in top])


def run_reference(output, path=None):
    """Write the reference for the frozen A8 model from its development run."""
    from . import account_score, meta
    from .playbook_research import load_hits
    model = account_score.load_model()
    if model is None:
        raise RuntimeError('no frozen score model')
    hits = load_hits(output)
    train = account_score.training_set(hits).copy()
    train = train.assign(_t=pd.to_datetime(train.time, utc=True)).sort_values('_t')
    x = meta.matrix(train).reindex(columns=model['columns'])
    wf = account_score.walk_forward(hits)
    ref = build_reference(x, model['hash'], wf[['p', 'o_net']])
    ref.update(run=str(output), trained_until=model.get('trained_until'), generated=pd.Timestamp.now(tz='UTC').isoformat())
    path = Path(path or reference_path())
    path.write_text(json.dumps(ref, indent=1), encoding='utf-8')
    return ref
