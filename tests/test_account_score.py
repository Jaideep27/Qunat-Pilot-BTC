"""A8: the frozen score model behind the four paper accounts, its cutoffs and development expectations."""
import numpy as np
import pandas as pd

from quantpilot import account_score, meta


def hits(n=1200, seed=0):
    """Synthetic confirmed setups over 2021-10 .. 2025-10; f_vwap_z decides the outcome."""
    rng = np.random.default_rng(seed)
    times = pd.date_range('2021-10-21', '2025-10-15', periods=n, tz='UTC').floor('5min')
    x = rng.normal(0, 1, n)
    net = np.where(x > 0, .012, -.010)
    frame = pd.DataFrame(dict(id=[f'PB08L:{t.isoformat()}' for t in times], playbook='PB08L', family='trend',
                              side='long', time=times, regime='RANGE', status='PLAN_REJECTED', tradeable=False,
                              reasons=[np.array(['REWARD_RISK_BELOW_MIN'])]*n, plan_stop=1.0, priority=2,
                              confluence=rng.integers(-3, 4, n), plan_reward_risk=1.2, plan_risk_fraction=.008,
                              o_valid=True, o_net=net, o_r=net/.008,
                              o_entry_time=[str(t+pd.Timedelta(minutes=5)) for t in times],
                              o_exit_time=[str(t+pd.Timedelta(hours=2)) for t in times]))
    for c in meta.FEATURES:
        if c not in frame:
            frame[c] = rng.normal(0, 1, n)
    frame['f_vwap_z'] = x
    return frame


def test_training_set_keeps_rr_rejections_and_drops_structural_ones():
    h = hits(8)
    h.loc[0, 'status'], h.loc[0, 'tradeable'] = 'TAKEN', True
    h.at[0, 'reasons'] = np.array([], dtype=object)
    h.at[2, 'reasons'] = np.array(['INVALID_LEVELS'])
    h.at[3, 'reasons'] = np.array(['STOP_TOO_WIDE'])
    h.loc[4, 'status'], h.loc[4, 'plan_stop'] = 'NOT_CONFIRMED', np.nan
    kept = set(account_score.training_set(h).index)
    assert kept == {0, 1, 5, 6, 7}


def test_training_set_excludes_holdout():
    h = hits(4)
    h.loc[3, 'o_exit_time'] = '2025-10-21 00:00:00+00:00'
    assert 3 not in set(account_score.training_set(h).index)


def test_score_formula():
    model = account_score.fit(account_score.training_set(hits()))

    class Fixed:
        def predict_proba(self, x):
            return np.column_stack([1-self.p, self.p])
    stub = dict(model, model=Fixed())
    frame = hits(2).assign(plan_reward_risk=[2.0, 1.0])
    stub['model'].p = np.array([.5, .25])
    out = account_score.score(stub, frame)
    assert np.allclose(out.score, [.5, -.5]) and np.allclose(out.p, [.5, .25])


def test_score_tolerates_missing_features():
    model = account_score.fit(account_score.training_set(hits()))
    row = hits(1)
    row['f_vwap_z'] = np.nan
    row['f_global_ratio'] = np.nan
    out = account_score.score(model, row)
    assert np.isfinite(out.score.iloc[0])
    assert {'f_vwap_z', 'f_global_ratio'} <= set(out.missing.iloc[0])


def test_cutoffs_are_percentiles():
    scores = pd.Series(np.arange(1, 101, dtype=float))
    out = account_score.cutoffs(scores, {'acct-a': 90, 'acct-d': 50})
    assert np.isclose(out['acct-a'], np.percentile(scores, 90)) and np.isclose(out['acct-d'], np.percentile(scores, 50))


def test_expectations_one_position_and_half_risk():
    t = pd.Timestamp('2023-01-02', tz='UTC')
    wf = pd.DataFrame(dict(time=[t, t+pd.Timedelta(minutes=30), t+pd.Timedelta(hours=5)],
                           o_entry_time=[t, t+pd.Timedelta(minutes=30), t+pd.Timedelta(hours=5)],
                           o_exit_time=[t+pd.Timedelta(hours=2), t+pd.Timedelta(hours=3), t+pd.Timedelta(hours=6)],
                           o_r=[1.0, 1.0, -1.0], o_net=[.01, .01, -.01], score=[.9, .9, .2]))
    cfg = [dict(id='acct-x', risk=.01, cutoff_score=.1, full_risk_score=.5)]
    out = account_score.expectations(wf, cfg)['acct-x']
    assert out['trades'] == 2                                # the overlapping second setup is skipped
    assert np.isclose(out['final_equity'], 10_000*1.01*(1-.005))   # third is below the full-risk line: half risk
