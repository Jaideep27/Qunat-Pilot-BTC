"""M3: the ML filter (meta-labeling) on playbook hits, its pre-registered gate and the live filter."""
import numpy as np
import pandas as pd
import pytest

from quantpilot import meta, rules


def hits(n=1600, signal=True, seed=0, playbook='PB09L'):
    """Synthetic tradeable hits over 2021-10 .. 2025-10; with `signal`, f_vwap_z decides the outcome."""
    rng = np.random.default_rng(seed)
    times = pd.date_range('2021-10-21', '2025-10-15', periods=n, tz='UTC').floor('5min')
    x = rng.normal(0, 1, n)
    win = (x > 0) if signal else (rng.random(n) < .5)
    net = np.where(win, .012, -.010)+rng.normal(0, .001, n)
    frame = pd.DataFrame(dict(id=[f'{playbook}:{t.isoformat()}' for t in times], playbook=playbook, family='technical',
                              side='long', time=times, regime='RANGE', status='TAKEN', tradeable=True, priority=2,
                              confluence=rng.integers(-3, 4, n), stressed=False, plan_reward_risk=1.6,
                              plan_risk_fraction=.008, o_valid=True, o_net=net, o_r=net/.008,
                              o_entry_time=[str(t+pd.Timedelta(minutes=5)) for t in times],
                              o_exit_time=[str(t+pd.Timedelta(hours=2)) for t in times], o_reason='TARGET'))
    for c in meta.FEATURES:
        frame[c] = rng.normal(0, 1, n)
    frame['f_vwap_z'] = x
    return frame


def test_features_are_stable_and_include_trigger_facts_and_plan():
    assert 'f_vwap_z' in meta.FEATURES and 'tf_oi_change' in meta.FEATURES and 'plan_reward_risk' in meta.FEATURES
    assert len(meta.FEATURES) == len(set(meta.FEATURES))


def test_a_real_signal_earns_live_mode_and_noise_does_not():
    book = rules.load()
    good = meta.evaluate(book, hits(signal=True))
    result = good['playbooks']['PB09L']
    assert result['filtered']['calls'] >= 30 and result['difference']['low'] > 0 and result['live']
    noise = meta.evaluate(book, hits(signal=False, seed=3))
    assert not noise['playbooks']['PB09L']['live']


def test_training_never_uses_hits_that_exit_after_the_block_starts():
    frame = hits()
    model = meta.fit(frame, pd.Timestamp('2023-04-20', tz='UTC'))
    assert model is not None and model['trained_until'] <= '2023-04-20' and model['n'] < len(frame)
    assert model['n'] == int((pd.to_datetime(frame.o_exit_time, utc=True) <= pd.Timestamp('2023-04-20', tz='UTC')).sum())


def test_small_playbooks_pool_by_family_and_tiny_families_get_no_model():
    frame = pd.concat([hits(n=1500, playbook='PB09L'), hits(n=60, playbook='PB09S', seed=5).assign(side='short')])
    book = rules.load()
    out = meta.evaluate(book, frame)
    assert out['playbooks']['PB09S']['scope'] == 'family'
    assert meta.fit(hits(n=50), pd.Timestamp('2025-10-20', tz='UTC')) is None


def test_live_filter_round_trip(tmp_path, monkeypatch):
    frame = hits()
    path = meta.train_final(frame, tmp_path/'meta-models.joblib')
    monkeypatch.setattr(meta, 'MODELS', path)
    fn = meta.live_filter()
    features = {c[2:]: 2.0 for c in meta.FEATURES if c.startswith('f_')} | {'vwap_z': 2.0}
    hit = dict(playbook='PB09L', family='technical', confluence=dict(score=1), regime='RANGE',
               plan=dict(reward_risk=1.6, risk_fraction=.008), trigger=dict(facts={}))
    up = fn(hit, features)
    down = fn(hit, features | {'vwap_z': -2.0})
    assert up['verdict'] == 'take' and down['verdict'] == 'skip' and up['p'] > .5 > down['p']
    assert fn(dict(hit, playbook='PB01', family='dump'), features)['verdict'] == 'none'
