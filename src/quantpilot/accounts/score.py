"""A8: the frozen score model behind the four paper accounts, their cutoffs and development expectations.

Every confirmed v2 setup with a plan gets `p`, the probability that it ends with a net profit (one L2 logistic
regression on the M3 feature list), and `score = p*RR - (1-p)`, its expected net result in R. Cutoffs are fixed
percentiles of the walk-forward development scores; the live model is one final fit, frozen. Only development
hits (exit before 2025-10-20) are read; the holdout is untouched.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from quantpilot.rules import meta
from quantpilot.config import settings
from quantpilot.research.playbooks import load_hits
from quantpilot.research.strategies import DEV_END, blocks



def model_path():
    return settings.artifact_dir/'rule-engine'/'accounts-score.joblib'


def report_path():
    return settings.artifact_dir/'rule-engine'/'accounts-report.json'


CONFIG = Path(__file__).resolve().parents[3]/'rulebook'/'accounts-v1.yaml'
ALLOWED = {'REWARD_RISK_BELOW_MIN'}
BALANCE = 10_000.0
# The registered A8 table.
ACCOUNTS = [
    dict(id='acct-a', name='Careful', percentile=90, full_risk_percentile=95, leverage=2.0, risk=.01, daily_brake=.03),
    dict(id='acct-b', name='Steady', percentile=70, full_risk_percentile=85, leverage=3.0, risk=.015, daily_brake=.045),
    dict(id='acct-c', name='Bold', percentile=60, full_risk_percentile=80, leverage=4.0, risk=.02, daily_brake=.06),
    dict(id='acct-d', name='Aggressive', percentile=50, full_risk_percentile=75, leverage=5.0, risk=.025, daily_brake=.075),
]


def _reasons(value):
    return set() if value is None or (isinstance(value, float) and np.isnan(value)) else set(map(str, np.atleast_1d(value)))


def training_set(hits):
    """Confirmed setups with a plan and a valid development outcome, rejected at most for reward:risk."""
    h = hits[hits.plan_stop.notna() & (hits.o_valid == True) & hits.o_net.notna()]  # noqa: E712
    h = h[pd.to_datetime(h.o_exit_time, utc=True) < DEV_END]
    return h[h.reasons.map(lambda r: _reasons(r) <= ALLOWED)]


def fit(train):
    x = meta.matrix(train)
    x = x.loc[:, x.notna().any()]
    model = make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    model.fit(x, (train.o_net > 0).astype(int))
    until = pd.to_datetime(train.o_exit_time, utc=True).max()
    return dict(model=model, columns=list(x.columns), n=int(len(train)), trained_until=str(until.date()))


def score(model, frame):
    """`p`, `score` and the model inputs missing from each row (they are imputed with training medians)."""
    x = meta.matrix(frame).reindex(columns=model['columns'])
    p = model['model'].predict_proba(x)[:, 1]
    rr = pd.to_numeric(frame['plan_reward_risk'], errors='coerce').to_numpy(float)
    missing = [list(x.columns[row]) for row in x.isna().to_numpy()]
    return pd.DataFrame(dict(p=p, score=p*rr-(1-p), missing=missing), index=frame.index)


def walk_forward(hits):
    """Out-of-sample scores: each 6-month block scored by a model fit on setups that exited before it."""
    train = training_set(hits).copy()
    train['time'] = pd.to_datetime(train.time, utc=True)
    exits = pd.to_datetime(train.o_exit_time, utc=True)
    parts = []
    for start, end in blocks(train.time.min()):
        test = train[(train.time >= start) & (train.time < end)]
        past = train[exits < start]
        if test.empty or past.o_net.gt(0).nunique() < 2:
            continue
        parts.append(test.join(score(fit(past), test)[['p', 'score']]))
    return pd.concat(parts) if parts else train.iloc[0:0].assign(p=[], score=[])


def cutoffs(scores, percentiles):
    return {k: float(np.percentile(scores, q)) for k, q in percentiles.items()}


def expectations(wf, config):
    """Each account replayed on the walk-forward setups: one position at a time, half risk below full risk."""
    wf = wf.sort_values('time')
    entries, exits = pd.to_datetime(wf.o_entry_time, utc=True), pd.to_datetime(wf.o_exit_time, utc=True)
    weeks = max((pd.to_datetime(wf.time, utc=True).max()-pd.to_datetime(wf.time, utc=True).min())/pd.Timedelta(weeks=1), 1)
    out = {}
    for acct in config:
        equity = peak = BALANCE
        worst, busy, results = 0.0, None, []
        for entry, exit_, r, net, s in zip(entries, exits, wf.o_r, wf.o_net, wf.score):
            if s < acct['cutoff_score'] or (busy is not None and entry < busy):
                continue
            risk = acct['risk'] if s >= acct['full_risk_score'] else acct['risk']/2
            equity *= 1+risk*r
            peak = max(peak, equity)
            worst = min(worst, equity/peak-1)
            busy = exit_
            results.append((r, net))
        n = len(results)
        out[acct['id']] = dict(trades=n, trades_per_week=n/weeks, win_rate=float(np.mean([x[1] > 0 for x in results])) if n else None,
                               mean_r=float(np.mean([x[0] for x in results])) if n else None, worst_drawdown=worst,
                               final_equity=equity)
    return out


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def write_config(accounts, model_hash, path=CONFIG):
    header = ('# Four paper accounts (pre-registration A8). Generated by `quantpilot research-accounts`.\n'
              '# cutoff_score / full_risk_score are the walk-forward development score percentiles, resolved once.\n'
              '# Changing anything here makes accounts-v2 and needs a new amendment.\n')
    body = dict(version=1, rulebook='db3d684ff71c', round_length=30, floor=5000.0, balance=BALANCE,
                model=dict(path='artifacts/rule-engine/accounts-score.joblib', hash=model_hash), accounts=accounts)
    Path(path).write_text(header+yaml.safe_dump(body, sort_keys=False), encoding='utf-8')
    return path


def run(output, model_file=None, report_file=None, config_path=CONFIG):
    """Fit, resolve cutoffs, write the frozen model, `accounts-v1.yaml` and the expectations report."""
    model_file, report_file = model_file or model_path(), report_file or report_path()
    hits = load_hits(output)
    wf = walk_forward(hits)
    cut = cutoffs(wf.score, {a['id']: a['percentile'] for a in ACCOUNTS})
    full = cutoffs(wf.score, {a['id']: a['full_risk_percentile'] for a in ACCOUNTS})
    accounts = [dict(a, cutoff_score=round(cut[a['id']], 6), full_risk_score=round(full[a['id']], 6)) for a in ACCOUNTS]
    train = training_set(hits)
    final = fit(train)
    Path(model_file).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(final, model_file)
    model_hash = _hash(model_file)
    write_config(accounts, model_hash, config_path)
    report = dict(generated=pd.Timestamp.now(tz='UTC').isoformat(), run=str(output), training_setups=len(train),
                  walk_forward_setups=len(wf), trained_until=final['trained_until'], model_hash=model_hash,
                  calibration=_calibration(wf), cutoffs=cut, full_risk=full, expectations=expectations(wf, accounts))
    Path(report_file).write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    return report


def _calibration(wf):
    bins = pd.qcut(wf.p, 5, duplicates='drop')
    g = wf.groupby(bins, observed=True).agg(n=('p', 'size'), predicted=('p', 'mean'), actual=('o_net', lambda x: (x > 0).mean()),
                                            mean_r=('o_r', 'mean'))
    return [dict(n=int(r.n), predicted=float(r.predicted), actual=float(r.actual), mean_r=float(r.mean_r)) for r in g.itertuples()]


def load_model(path=None):
    """The frozen model with its file hash, or None when the file is missing or unreadable."""
    path = Path(path or model_path())
    try:
        return dict(joblib.load(path), hash=_hash(path)) if path.exists() else None
    except Exception:
        return None


def live_row(hit, features):
    """The research-hit columns for one live setup (same names and definitions as the research run)."""
    return meta._row(hit, features)
