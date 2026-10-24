"""Model health: reference statistics, data check, drift and calibration of the live score model."""
import json

import numpy as np
import pandas as pd

from quantpilot.monitoring import model_health as mh

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


def _hits_and_decisions(engine, n, start='2026-10-21', inputs=True, wins=None):
    from quantpilot.accounts import journal as account_journal
    from quantpilot.rules import journal
    times = pd.date_range(start, periods=n, freq='15min', tz='UTC')
    hits, rows = [], []
    for i, t in enumerate(times):
        hid = f'PB08S:{t.isoformat()}'
        hits.append(dict(id=hid, time=t, playbook='PB08S', side='short', status='PLAN_REJECTED',
                         reasons=['REWARD_RISK_BELOW_MIN']))
        payload = dict(inputs=dict(f_move_z_15m=float(i % 7), f_rsi_15m=40.0+i % 11, side='short', regime='TREND_DOWN',
                                   plan_reward_risk=1.5) if inputs else None,
                       data_check=[] if inputs else 'no inputs', challenger=None)
        for acct in ('acct-a', 'acct-b'):
            rows.append(dict(account=acct, hit_id=hid, time=t, decision='BELOW_CUTOFF', score=-.3, confidence=.4, cutoff=0,
                             payload=payload))
    journal.add_hits(engine, hits)
    account_journal.add_decisions(engine, rows)
    for i, h in enumerate(hits):
        won = (i % 5 < 2) if wins is None else wins
        journal.diagnose_hit(engine, h['id'], dict(valid=True, net=1.0 if won else -1.0, exit_time=(h['time']+pd.Timedelta(hours=2)).isoformat()),
                             'X', h['time']+pd.Timedelta(hours=6))
    return hits


def test_setups_count_each_hit_once_and_carry_outcomes(account):
    _hits_and_decisions(account, 12)
    s = mh.setups(account)
    assert len(s) == 12 and s.hit_id.is_unique
    assert s.won.sum() == sum(i % 5 < 2 for i in range(12)) and set(s.p) == {.4}
    assert s.inputs.iloc[0]['regime'] == 'TREND_DOWN'


def test_live_training_rows_need_inputs_and_a_valid_outcome(account):
    _hits_and_decisions(account, 6)
    _hits_and_decisions(account, 4, start='2026-11-01', inputs=False)
    rows = mh.live_training_rows(account)
    assert len(rows) == 6 and {'f_move_z_15m', 'o_net', 'o_exit_time', 'side'} <= set(rows.columns)


def _dev(n=300, seed=1):
    rng = np.random.default_rng(seed)
    z = rng.normal(size=n)
    return pd.DataFrame(dict(f_move_z_15m=z, f_rsi_15m=rng.normal(50, 10, n), side='short', regime='TREND_DOWN',
                             plan_reward_risk=1.5, o_net=np.where(z+rng.normal(size=n) > 0, 1.0, -1.0),
                             o_exit_time=pd.date_range('2022-01-01', periods=n, freq='D', tz='UTC').astype(str)))


def test_train_challenger_refuses_without_enough_new_setups(tmp_path):
    import pytest
    with pytest.raises(ValueError, match='not enough new setups: 3 of 200'):
        mh.train_challenger(_dev(), _dev(3), paths=(tmp_path/'c.joblib', tmp_path/'c.json'))


def test_train_challenger_writes_files_and_scores(tmp_path):
    from quantpilot.accounts import score as account_score
    paths = (tmp_path/'c.joblib', tmp_path/'c.json')
    info = mh.train_challenger(_dev(), _dev(20, seed=2), min_new=5, paths=paths)
    assert info['n_dev'] == 300 and info['n_live'] == 20 and len(info['hash']) == 12
    model = mh.load_challenger(paths)
    assert model['hash'] == info['hash']
    p = account_score.score(model, _dev(4, seed=3)).p
    assert ((p > 0) & (p < 1)).all()


def test_compare_verdicts():
    rng = np.random.default_rng(0)
    won = rng.random(100) < .4
    rows = pd.DataFrame(dict(p=np.full(100, .4), cp=np.where(won, .95, .05), won=won))
    assert mh.compare(rows.iloc[:10])['verdict'] == 'TOO_FEW'
    assert mh.compare(rows)['verdict'] == 'CHALLENGER_BETTER'
    assert mh.compare(rows.assign(cp=rows.p))['verdict'] == 'NO_DIFFERENCE'


def test_status_overall_rules():
    ok = dict(verdict='IN_LINE')
    calm = dict(verdict='STABLE')
    checks = dict(checked_7d=100, failed_7d=0, failed_24h=0)
    assert mh.overall(ok, calm, checks, True)[0] == 'OK'
    assert mh.overall(ok, dict(verdict='ALARM'), checks, True)[0] == 'ALARM'
    assert mh.overall(dict(verdict='OVERCONFIDENT', predicted=.5, actual=.3, n=60), calm, checks, True)[0] == 'ALARM'
    assert mh.overall(ok, calm, dict(checks, failed_7d=21), True)[0] == 'ALARM'
    assert mh.overall(ok, dict(verdict='WATCH'), checks, True)[0] == 'WATCH'
    assert mh.overall(ok, calm, checks, False)[0] == 'WATCH'
    state, reasons = mh.overall(ok, calm, dict(checks, failed_7d=1, failed_24h=1), True)
    assert state == 'WATCH' and any('data check' in r for r in reasons)
    assert mh.overall(dict(verdict='TOO_FEW'), dict(verdict='TOO_FEW'), dict(checked_7d=0, failed_7d=0, failed_24h=0), True)[0] == 'OK'


def test_status_on_journal(account):
    _hits_and_decisions(account, 40)
    out = mh.status(account, pd.Timestamp('2026-10-22', tz='UTC'))
    assert out['calibration']['n'] == 40 and out['calibration']['verdict'] == 'IN_LINE'
    assert out['reference'] == 'missing' and out['overall'] == 'WATCH'
    assert out['data_checks']['checked_7d'] == 40 and out['data_checks']['failed_7d'] == 0
    assert out['drift']['verdict'] == 'NO_REFERENCE' and out['challenger'] is None
    assert out['card']['model_hash'] and out['reasons']


def test_status_with_an_empty_journal(account):
    out = mh.status(account)
    assert out['calibration']['n'] == 0 and out['data_checks']['checked_7d'] == 0 and out['overall'] in ('OK', 'WATCH')
    assert len(mh.live_training_rows(account)) == 0
