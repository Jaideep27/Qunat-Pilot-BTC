"""M5: the learner, a full-information contextual bandit over playbook hits (amendment A6).

Context = (playbook, regime). Actions = skip (0 R), take (the normal plan) and take_wide (stop and target
distances x 1.67). Every hit's outcome under both plans is simulated, so each action's reward is observed
for every hit and no random exploration is needed. Values are shrinkage means (prior 0 R with 20
pseudo-observations); the policy takes the best action whose 80% lower bound is above zero, otherwise it
skips. It goes live only if, walk-forward, its mean R per hit beats "always take" with a bootstrap 95%
interval above zero. R is in each plan's own risk units, which the fixed 0.25% risk sizing makes comparable.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import settings
from .strategy_research import blocks

PRIOR_N, Z80, MIN_TRADES, BOOTSTRAP = 20, 1.2816, 30, 2000
ACTIONS = ('take', 'take_wide')
STATS = settings.artifact_dir/'rule-engine'/'learner.json'


def _usable(hits):
    return hits[hits.tradeable & (hits.o_valid == True) & hits.o_r.notna()]  # noqa: E712


def _cell(r):
    r = np.asarray(r, float)
    n = len(r)
    sd = float(np.std(r, ddof=1)) if n > 1 else 1.0
    mean = float(r.sum()/(n+PRIOR_N))
    return dict(n=int(n), mean=mean, sd=sd, low80=mean-Z80*sd/np.sqrt(n+PRIOR_N))


def statistics(hits, until=None):
    """{(playbook, regime): {'take': cell, 'take_wide': cell}} from hits that exited by `until`."""
    rows = _usable(hits)
    if until is not None:
        rows = rows[pd.to_datetime(rows.o_exit_time, utc=True) <= pd.Timestamp(until)]
    out = {}
    for (pid, regime), group in rows.groupby(['playbook', 'regime']):
        wide = group.w_r.where(group.get('w_valid', True) == True, group.o_r)  # noqa: E712
        out[(pid, regime)] = dict(take=_cell(group.o_r), take_wide=_cell(wide.fillna(group.o_r)))
    return out


def choose(stats, playbook, regime):
    cell = stats.get((playbook, regime))
    if not cell:
        return dict(action='skip', reason='no history for this playbook and regime')
    good = [(cell[a]['mean'], a) for a in ACTIONS if cell[a]['low80'] > 0]
    if not good:
        return dict(action='skip', reason='no action has an 80% lower bound above 0 R', **{a: cell[a] for a in ACTIONS})
    best = max(good)[1]
    return dict(action=best, **{a: cell[a] for a in ACTIONS})


def _paired(diff, reps=BOOTSTRAP, seed=0):
    rng = np.random.default_rng(seed)
    diff = np.asarray(diff, float)
    means = rng.choice(diff, (reps, len(diff))).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def evaluate(book, hits):
    """Walk-forward: per 6-month block, the policy learned from earlier exits against always-take."""
    rows = _usable(hits).copy()
    if rows.empty:
        return dict(live=False, learner_trades=0, difference=None, blocks=[])
    rows['time'] = pd.to_datetime(rows.time, utc=True)
    learned, base, chosen, record = [], [], [], []
    for start, end in blocks(rows.time.min()):
        test = rows[(rows.time >= start) & (rows.time < end)]
        if test.empty:
            continue
        stats = statistics(rows, until=start)
        counts = dict(take=0, take_wide=0, skip=0)
        for hit in test.itertuples():
            action = choose(stats, hit.playbook, hit.regime)['action']
            counts[action] += 1
            wide = hit.w_r if getattr(hit, 'w_valid', True) and pd.notna(hit.w_r) else hit.o_r
            learned.append({'take': hit.o_r, 'take_wide': wide, 'skip': 0.0}[action])
            base.append(hit.o_r)
            chosen.append(action)
        record.append(dict(block=str(start.date()), hits=len(test), **counts))
    diff = np.asarray(learned)-np.asarray(base)
    low, high = _paired(diff) if len(diff) > 1 else (None, None)
    trades = sum(a != 'skip' for a in chosen)
    live = bool(low is not None and low > 0 and trades >= MIN_TRADES)
    return dict(live=live, learner_trades=int(trades), hits=len(base), learner_mean_r=float(np.mean(learned)),
                always_take_mean_r=float(np.mean(base)), difference=dict(mean=float(diff.mean()), low=low, high=high),
                blocks=record, actions={a: chosen.count(a) for a in ('take', 'take_wide', 'skip')})


def _raw(cell):
    """Count, sum and sum of squares behind a stored cell (the shrinkage mean and sd determine them)."""
    n = cell['n']
    total = cell['mean']*(n+PRIOR_N)
    squares = cell['sd']**2*(n-1)+(total**2/n if n else 0.0) if n > 1 else total**2
    return n, total, squares


def merge(stats, rows):
    """Add live journal outcomes (taken trades and simulated skips) to the 'take' action of their cell.
    The wide-stop alternative is not observed live, so it keeps its historical value."""
    out = {k: dict(v) for k, v in stats.items()}
    for row in rows:
        outcome, regime = row.get('outcome') or {}, (row.get('payload') or {}).get('regime')
        r = outcome.get('r')
        if r is None or not outcome.get('valid', True) or regime is None:
            continue
        key = (row['playbook'], regime)
        cell = out.setdefault(key, dict(take=_cell([]), take_wide=_cell([])))
        n, total, squares = _raw(cell['take'])
        n, total, squares = n+1, total+r, squares+r*r
        mean = total/(n+PRIOR_N)
        sd = float(np.sqrt(max(0.0, (squares-total**2/n)/(n-1)))) if n > 1 else 1.0
        cell['take'] = dict(n=n, mean=mean, sd=sd, low80=mean-Z80*sd/np.sqrt(n+PRIOR_N))
    return out


def save(stats, path=STATS):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({f'{p}|{r}': v for (p, r), v in stats.items()}, indent=2), encoding='utf-8')
    return path


def load(path=STATS):
    stored = json.loads(Path(path).read_text(encoding='utf-8'))
    return {tuple(k.split('|', 1)): v for k, v in stored.items()}


def live_policy():
    """`(hit, features) -> verdict` from the stored statistics, or None before they exist."""
    if not Path(STATS).exists():
        return None
    stats = load(STATS)
    try:   # keep learning from the live journal: every diagnosed setup and trade updates its cell
        from . import journal
        from .store import database
        stats = merge(stats, [h for h in journal.hits(database(settings.market_database_url or settings.database_url),
                                                       limit=5000) if h.get('outcome')])
    except Exception:
        pass
    def verdict(hit, features):
        pick = choose(stats, hit.get('playbook'), hit.get('regime'))
        return dict(verdict=pick['action'], reason=pick.get('reason'),
                    take=pick.get('take', {}).get('mean'), take_wide=pick.get('take_wide', {}).get('mean'))
    return verdict


def run(output, book=None):
    """Evaluate the learner on a research run's hits; write learner-report.json, live modes and statistics."""
    from . import rules
    from .meta import write_modes
    from .playbook_research import load_hits
    book = book or rules.load()
    hits = load_hits(output)
    result = evaluate(book, hits)
    playbooks = sorted(_usable(hits).playbook.unique()) if len(hits) else []
    result['live'] = playbooks if result['live'] else []
    result['summary'] = 'live for every playbook' if result['live'] else 'kept in shadow (did not beat always-take)'
    result['generated'] = pd.Timestamp.now(tz='UTC').isoformat()
    (Path(output)/'learner-report.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    write_modes('learner', result['live'])
    save(statistics(hits))
    return result
