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


LIVE_START = pd.Timestamp('2026-10-20', tz='UTC')    # after the holdout: live setups a challenger may learn from
MIN_NEW, MIN_COMPARE = 200, 50
_cache = {}


def _utc(value):
    t = pd.Timestamp(value)
    return t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')


def setups(engine):
    """One row per scored live setup (the accounts share it): `p`, inputs, data check, challenger and outcome."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from .account_journal import AccountDecision
    from .journal import RuleHit
    with Session(engine) as session:
        rows = session.execute(select(AccountDecision.hit_id, AccountDecision.time, AccountDecision.confidence,
                                      AccountDecision.score, AccountDecision.decision, AccountDecision.payload)
                               .order_by(AccountDecision.time, AccountDecision.id)).all()
        ids = list(dict.fromkeys(r[0] for r in rows))
        hits = {h.id: h for h in session.scalars(select(RuleHit).where(RuleHit.id.in_(ids)))} if ids else {}
        out, seen = [], set()
        for hit_id, time, p, score, decision, payload in rows:
            if hit_id in seen:
                continue
            seen.add(hit_id)
            hit, payload = hits.get(hit_id), payload or {}
            outcome = (hit.outcome if hit is not None else None) or {}
            resolved = bool(outcome.get('valid')) and outcome.get('net') is not None
            out.append(dict(hit_id=hit_id, time=_utc(time), p=p, score=score, decision=decision,
                            inputs=payload.get('inputs'), data_check=payload.get('data_check'),
                            challenger=payload.get('challenger'), resolved=resolved,
                            won=bool(resolved and outcome['net'] > 0), net=outcome.get('net'),
                            exit_time=outcome.get('exit_time'),
                            reasons=list(((hit.payload if hit is not None else None) or {}).get('reasons') or [])))
    columns = ['hit_id', 'time', 'p', 'score', 'decision', 'inputs', 'data_check', 'challenger', 'resolved', 'won', 'net',
               'exit_time', 'reasons']
    return pd.DataFrame(out, columns=columns)


def live_training_rows(engine, frame=None):
    """Resolved live setups after the holdout with recorded inputs, filtered as the A8 training set was."""
    from .account_score import ALLOWED
    s = setups(engine) if frame is None else frame
    s = s[((s.time >= LIVE_START) & s.inputs.notna() & s.resolved & s.reasons.map(lambda r: set(r) <= ALLOWED)).astype(bool)]
    rows = pd.DataFrame(list(s.inputs), index=s.index)
    return rows.assign(o_net=s.net.astype(float), o_exit_time=s.exit_time, time=s.time).reset_index(drop=True)


def challenger_paths():
    base = settings.artifact_dir/'rule-engine'
    return base/'challenger.joblib', base/'challenger.json'


def train_challenger(dev_train, live_rows, min_new=MIN_NEW, now=None, paths=None):
    """Refit the A8 recipe on development setups plus new live setups. Shadow only: it never decides."""
    import joblib
    from . import account_score
    if len(live_rows) < min_new:
        raise ValueError(f'not enough new setups: {len(live_rows)} of {min_new}')
    model_file, meta_file = paths or challenger_paths()
    fitted = account_score.fit(pd.concat([dev_train, live_rows], ignore_index=True))
    Path(model_file).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(fitted, model_file)
    info = dict(hash=account_score._hash(model_file), n_dev=int(len(dev_train)), n_live=int(len(live_rows)),
                trained_until=fitted['trained_until'], created=_utc(now or pd.Timestamp.now(tz='UTC')).isoformat())
    Path(meta_file).write_text(json.dumps(info, indent=1), encoding='utf-8')
    return info


def challenger_info(paths=None):
    try:
        return json.loads(Path((paths or challenger_paths())[1]).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def load_challenger(paths=None):
    """The shadow challenger model with its file hash (reloaded when the file changes), or None."""
    from . import account_score
    model_file = Path((paths or challenger_paths())[0])
    try:
        key = (str(model_file), model_file.stat().st_mtime) if model_file.exists() else None
        if key is None:
            return None
        if _cache.get('challenger_key') != key:
            import joblib
            _cache.update(challenger_key=key, challenger=dict(joblib.load(model_file), hash=account_score._hash(model_file)))
        return _cache['challenger']
    except Exception:
        return None


def compare(rows, reps=2000, seed=0):
    """Live model against the challenger on the same resolved setups: Brier scores and a paired bootstrap."""
    won = rows.won.astype(float).to_numpy()
    a, b = (rows.p.to_numpy(float)-won)**2, (rows.cp.to_numpy(float)-won)**2
    n = int(len(rows))
    out = dict(n=n, brier=float(a.mean()) if n else None, challenger_brier=float(b.mean()) if n else None,
               diff_lo=None, diff_hi=None)
    if n < MIN_COMPARE:
        return dict(out, verdict='TOO_FEW')
    d = b-a
    idx = np.random.default_rng(seed).integers(0, n, size=(reps, n))
    lo, hi = np.percentile(d[idx].mean(axis=1), [2.5, 97.5])
    return dict(out, diff_lo=float(lo), diff_hi=float(hi), verdict='CHALLENGER_BETTER' if hi < 0 else 'NO_DIFFERENCE')


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


def overall(cal, drift_, checks, reference_ok):
    """The single status (OK, WATCH or ALARM) and plain-English reasons."""
    alarm, watch, info = [], [], []
    if cal.get('verdict') == 'OVERCONFIDENT':
        alarm.append(f"The model has been too optimistic: it gave an average win chance of {cal['predicted']:.0%}, "
                     f"but {cal['actual']:.0%} of {cal['n']} setups actually won.")
    elif cal.get('verdict') == 'UNDERCONFIDENT':
        info.append(f"The model has been too cautious: average win chance {cal['predicted']:.0%}, "
                    f"actual {cal['actual']:.0%} of {cal['n']} setups.")
    elif cal.get('verdict') == 'TOO_FEW':
        info.append(f"Not enough finished setups yet to judge its accuracy ({cal.get('n', 0)} of {MIN_CALIBRATION}).")
    if drift_.get('verdict') == 'ALARM':
        alarm.append(f"The market looks unlike what the model learned from: {drift_.get('count')} inputs are unusual "
                     f"(an ordinary stretch has up to {drift_.get('usual') or 0:.0f}).")
    elif drift_.get('verdict') == 'WATCH':
        watch.append(f"The market looks somewhat different from what the model learned from: {drift_.get('count')} inputs "
                     f"are unusual (an ordinary stretch has up to {drift_.get('usual') or 0:.0f}).")
    elif drift_.get('verdict') == 'TOO_FEW':
        info.append(f"Not enough recorded setups yet to check for market change ({drift_.get('n', 0)} of "
                    f"{drift_.get('window', WINDOW)}).")
    if checks['checked_7d'] and checks['failed_7d']/checks['checked_7d'] > .2:
        alarm.append(f"{checks['failed_7d']} of {checks['checked_7d']} setups in the last 7 days failed the data check "
                     'and were held back.')
    elif checks['failed_24h']:
        watch.append(f"{checks['failed_24h']} setup(s) failed the data check in the last 24 hours and were held back.")
    if not reference_ok:
        watch.append("The model's reference file is missing, so the market-change and data checks are not running.")
    state = 'ALARM' if alarm else 'WATCH' if watch else 'OK'
    return state, alarm+watch+(info or [])+([] if alarm or watch else ['No warning signs.'])


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def status(engine, now=None):
    """Everything the Model health panel shows."""
    from . import account_score, accounts, meta
    now = _utc(now or pd.Timestamp.now(tz='UTC'))
    cfg = accounts.load()
    model = account_score.load_model()
    report = _json(account_score.report_path())
    ref = load_reference(cfg['model']['hash'])
    s = setups(engine)
    resolved = s[(s.resolved & s.p.notna()).astype(bool)]
    cal = calibration(pd.DataFrame(dict(p=resolved.p.astype(float), won=resolved.won)), ref)
    with_inputs = s[s.inputs.notna()]
    if ref is None:
        drift_ = dict(verdict='NO_REFERENCE', n=int(len(with_inputs)), top=[])
    else:
        x = meta.matrix(pd.DataFrame(list(with_inputs.inputs))) if len(with_inputs) else pd.DataFrame()
        drift_ = drift(x.reindex(columns=list(ref['inputs'])), ref)
    checked = s[s.data_check.map(lambda c: isinstance(c, list)).astype(bool)]
    recent = checked[checked.time > now-pd.Timedelta(days=7)]
    failed = recent[recent.data_check.map(bool).astype(bool)]
    latest = failed.iloc[-1] if len(failed) else None
    checks = dict(checked_7d=int(len(recent)), failed_7d=int(len(failed)),
                  failed_24h=int((failed.time > now-pd.Timedelta(hours=24)).sum()),
                  skipped_7d=int(((s.time > now-pd.Timedelta(days=7)) & ~s.data_check.map(lambda c: isinstance(c, list)).astype(bool)).sum()),
                  latest=None if latest is None else dict(time=latest.time.isoformat(), setup=latest.hit_id.split(':')[0],
                                                          reasons=latest.data_check))
    info = challenger_info()
    challenger = None
    if info:
        shadow = s[(s.resolved & s.challenger.map(lambda c: isinstance(c, dict) and c.get('hash') == info['hash'])).astype(bool)]
        challenger = dict(info, compare=compare(pd.DataFrame(dict(p=shadow.p.astype(float), won=shadow.won,
                                                                  cp=[c['p'] for c in shadow.challenger]))))
    state, reasons = overall(cal, drift_, checks, ref is not None)
    card = dict(model_hash=cfg['model']['hash'], file_hash=(model or {}).get('hash'),
                matches=bool(model) and model['hash'] == cfg['model']['hash'], config_hash=cfg.get('hash'),
                kind='L2 logistic regression (win chance of a confirmed setup)', inputs=len((model or {}).get('columns', [])),
                training_setups=report.get('training_setups'), trained_from='2021-10-20',
                trained_until=report.get('trained_until') or (model or {}).get('trained_until'),
                walk_forward_setups=report.get('walk_forward_setups'), walk_forward=report.get('calibration'),
                dev_brier=(ref or {}).get('brier'), held_back_in_training=(ref or {}).get('blocked_share'),
                live_since=s.time.min().isoformat() if len(s) else None, scored_setups=int(len(s)),
                holdout='2025-10-20 → 2026-10-19, untouched')
    return dict(overall=state, reasons=reasons, card=card, calibration=cal, drift=drift_, data_checks=checks,
                challenger=challenger, reference='ok' if ref is not None else 'missing', checked_at=now.isoformat())
