"""M5: the full-information contextual bandit over playbook hits and its pre-registered gate."""
import numpy as np
import pandas as pd
import pytest

from quantpilot import learner, rules


def hits(n=2000, seed=0, good_regime='TREND_UP'):
    """Two regimes: 'take' wins in `good_regime`, loses in RANGE; 'take_wide' is always slightly worse."""
    rng = np.random.default_rng(seed)
    times = pd.date_range('2021-10-21', '2025-10-15', periods=n, tz='UTC').floor('5min')
    regime = np.where(rng.random(n) < .5, good_regime, 'RANGE')
    r = np.where(regime == good_regime, rng.normal(.4, 1, n), rng.normal(-.4, 1, n))
    return pd.DataFrame(dict(id=[f'PB01:{t.isoformat()}' for t in times], playbook='PB01', family='dump', side='long',
                             time=times, regime=regime, tradeable=True, o_valid=True, w_valid=True, o_r=r, w_r=r-.1,
                             o_net=r*.01, o_exit_time=[str(t+pd.Timedelta(hours=3)) for t in times]))


def test_posterior_shrinks_small_cells_toward_zero():
    stats = learner.statistics(hits(n=30))
    cell = stats[('PB01', 'TREND_UP')]['take']
    raw = hits(n=30).query("regime == 'TREND_UP'").o_r.mean()
    assert abs(cell['mean']) < abs(raw) and cell['n'] == (hits(n=30).regime == 'TREND_UP').sum()


def test_policy_takes_where_the_lower_bound_is_positive_and_skips_elsewhere():
    stats = learner.statistics(hits())
    assert learner.choose(stats, 'PB01', 'TREND_UP')['action'] == 'take'
    assert learner.choose(stats, 'PB01', 'RANGE')['action'] == 'skip'
    assert learner.choose(stats, 'PB99', 'RANGE')['action'] == 'skip'        # unseen: prior only


def test_walk_forward_gate_passes_a_real_regime_effect_and_rejects_noise():
    book = rules.load()
    real = learner.evaluate(book, hits())
    assert real['difference']['low'] > 0 and real['live'] and real['learner_trades'] >= 30
    flat = hits(seed=4)
    flat['o_r'] = np.random.default_rng(9).normal(0, 1, len(flat))
    flat['w_r'] = flat.o_r
    noise = learner.evaluate(book, flat)
    assert not noise['live']


def test_training_uses_only_hits_that_exited_before_each_block():
    frame = hits()
    stats = learner.statistics(frame, until=pd.Timestamp('2023-01-01', tz='UTC'))
    used = sum(c['take']['n'] for c in stats.values())
    assert used == int((pd.to_datetime(frame.o_exit_time, utc=True) <= pd.Timestamp('2023-01-01', tz='UTC')).sum())


def test_live_policy_round_trip(tmp_path, monkeypatch):
    path = learner.save(learner.statistics(hits()), tmp_path/'learner.json')
    monkeypatch.setattr(learner, 'STATS', path)
    fn = learner.live_policy()
    assert fn(dict(playbook='PB01', regime='TREND_UP'), {})['verdict'] == 'take'
    assert fn(dict(playbook='PB01', regime='RANGE'), {})['verdict'] == 'skip'
