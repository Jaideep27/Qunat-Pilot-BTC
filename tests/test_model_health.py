"""Model health: reference statistics, data check, drift and calibration of the live score model."""
import json

import numpy as np
import pandas as pd

from quantpilot import model_health as mh

COLS = ['a', 'b', 'c', 'd', 'e', 'f']


def training(n=300, seed=0):
    rng = np.random.default_rng(seed)
    x = pd.DataFrame(rng.normal(size=(n, len(COLS))), columns=COLS)
    x.loc[x.index % 2 == 0, 'b'] = np.nan          # b: missing half the time, so not critical
    x.loc[[5], 'a'] = np.nan                       # a: present in 99.7% of rows, so critical
    return x


def reference(x=None):
    x = training() if x is None else x
    wf = pd.DataFrame(dict(p=np.full(len(x), .4), o_net=np.where(np.arange(len(x)) % 5 < 2, 1.0, -1.0)))
    return mh.build_reference(x, 'hash1', wf)


def test_psi_zero_for_same_distribution_and_large_for_shift():
    x = training()
    ref = reference(x)
    assert mh.psi(x['c'], ref['inputs']['c']) < .02
    assert mh.psi(x['c']+3*x['c'].std(), ref['inputs']['c']) > 1


def test_psi_counts_an_input_that_is_always_missing():
    ref = reference()
    assert mh.psi(pd.Series([np.nan]*50), ref['inputs']['c']) > 1


def test_reference_marks_critical_inputs_and_blocked_share():
    x = training()
    ref = reference(x)
    assert 'a' in ref['critical'] and 'b' not in ref['critical']
    assert ref['blocked_share'] == x['a'].isna().mean()
    assert ref['model_hash'] == 'hash1' and abs(ref['brier']-np.mean((.4-np.array([1, 1, 0, 0, 0]*60))**2)) < 1e-9


def test_data_check_flags_inf_out_of_range_and_missing_critical():
    x = training()
    ref = reference(x)
    row = x.iloc[1].to_dict()
    assert mh.data_check(row, ref) == []
    assert mh.data_check(dict(row, c=np.inf), ref) == ['inf:c']
    span = x['d'].max()-x['d'].min()
    assert mh.data_check(dict(row, d=x['d'].max()+1.5*span), ref) == ['range:d']
    assert mh.data_check(dict(row, a=None), ref) == ['missing:a']
    assert mh.data_check(dict(row, b=None), ref) == []          # b was often missing in training too


def test_load_reference_ignores_another_models_file(tmp_path):
    path = tmp_path/'ref.json'
    path.write_text(json.dumps(reference()), encoding='utf-8')
    assert mh.load_reference('hash1', path)['model_hash'] == 'hash1'
    assert mh.load_reference('other', path) is None
    assert mh.load_reference('hash1', tmp_path/'missing.json') is None


def test_calibration_verdicts():
    ref = reference()

    def pairs(n, p, wins):
        return pd.DataFrame(dict(p=[p]*n, won=[True]*wins+[False]*(n-wins)))
    assert mh.calibration(pairs(20, .3, 6), ref)['verdict'] == 'TOO_FEW'
    over = mh.calibration(pairs(100, .7, 30), ref)
    assert over['verdict'] == 'OVERCONFIDENT' and over['actual'] == .3 and over['lo'] < .3 < over['hi']
    assert mh.calibration(pairs(100, .3, 30), ref)['verdict'] == 'IN_LINE'
    assert mh.calibration(pairs(100, .1, 30), ref)['verdict'] == 'UNDERCONFIDENT'
    assert [b['n'] for b in mh.calibration(pairs(100, .3, 30), ref)['bins']] == [100, 0, 0]


def test_drift_stable_on_training_like_rows_and_alarm_on_shifted():
    x = training()
    ref = reference(x)
    fresh = training(50, seed=7)
    assert mh.drift(fresh, ref)['verdict'] in ('STABLE', 'WATCH')
    shifted = fresh+5
    out = mh.drift(shifted, ref)
    assert out['verdict'] == 'ALARM' and out['n'] == 50 and len(out['top']) == 5
    assert mh.drift(fresh.iloc[:10], ref)['verdict'] == 'TOO_FEW'


def test_wilson_interval():
    lo, hi = mh.wilson(30, 100)
    assert .21 < lo < .22 and .39 < hi < .40
    assert mh.wilson(0, 0) == (None, None)
