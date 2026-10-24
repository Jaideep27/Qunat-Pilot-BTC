"""Walk-forward selection, meta-labeling, the pass rule and the single holdout run."""
import json

import numpy as np
import pandas as pd
import pytest

from quantpilot.research import selective
from quantpilot.research import strategies as sr

DEV_END = pd.Timestamp('2025-10-20', tz='UTC')


def test_blocks_are_six_months_anchored_to_the_development_end():
    blocks = sr.blocks(pd.Timestamp('2023-01-01', tz='UTC'))
    assert blocks[-1] == (DEV_END-pd.DateOffset(months=6), DEV_END)
    assert blocks[0][0] >= pd.Timestamp('2024-01-01', tz='UTC') and len(blocks) == 3


def calls(entries, nets, hold=pd.Timedelta(hours=1)):
    entries = pd.to_datetime(entries, utc=True)
    return pd.DataFrame(dict(entry_time=entries, exit_time=entries+hold, net=nets,
                             hit=(np.asarray(nets) > 0).astype(int)))


def test_walk_forward_picks_on_the_past_and_tests_on_the_block():
    block = [(pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2025-07-01', tz='UTC'))]
    past = [f'2024-12-{d:02d}' for d in range(1, 13)]
    good = calls(past+['2025-02-01'], [.01]*12+[-.02])
    bad = calls(past+['2025-02-01'], [-.01]*12+[.05])
    pooled, picks, _ = sr.walk_forward(dict(good=good, bad=bad), block)
    assert picks[0]['key'] == 'good' and pooled.net.tolist() == [-.02]


def test_training_calls_must_exit_before_the_block_starts():
    block = [(pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2025-07-01', tz='UTC'))]
    late = calls([f'2024-12-{d:02d}' for d in range(20, 32)], [.01]*12, hold=pd.Timedelta(days=30))
    pooled, picks, _ = sr.walk_forward(dict(late=late), block)
    assert picks[0]['key'] is None and pooled.empty


def test_pooled_calls_never_overlap():
    frame = calls(['2025-01-01 00:00', '2025-01-01 00:30', '2025-01-01 02:00'], [.01, .02, .03])
    assert sr.non_overlapping(frame).net.tolist() == [.01, .03]


def synthetic_hourly(n=24*365*5, seed=4):
    rng = np.random.default_rng(seed)
    index = pd.date_range(DEV_END-pd.Timedelta(hours=n-24*200), periods=n, freq='h', tz='UTC')
    close = 30000*np.exp(np.cumsum(rng.normal(0, .006, n)))
    noise = np.abs(rng.normal(0, .002, n))
    frame = pd.DataFrame(dict(close=close, high=close*(1+noise), low=close*(1-noise), funding_paid=0.0),
                         index=index)
    frame['clue'] = rng.normal(size=n)
    return frame


def peek_family():
    return dict(id='PEEK', track='intraday', grid=[dict(k=12)], horizons=(12,),
                fn=lambda f, k: np.sign(f.close.shift(-k)/f.close-1).fillna(0).astype(int))


def noise_family():
    def fn(f, seed):
        return pd.Series(np.random.default_rng(seed).choice([-1, 0, 0, 0, 0, 1], len(f)), index=f.index)
    return dict(id='NOISE', track='intraday', grid=[dict(seed=1), dict(seed=2)], horizons=(12,), fn=fn)


def test_run_rules_registers_every_configuration_and_gates(tmp_path):
    panel = synthetic_hourly()
    registry = tmp_path/'trials.jsonl'
    results = sr.run_rules({'intraday': panel}, [peek_family(), noise_family()], registry, tmp_path)
    assert selective.trial_count(registry, 'intraday') == 3
    gated = sr.gate_all(results, registry)
    assert gated['PEEK']['gate']['passed'] is True
    assert gated['NOISE']['gate']['passed'] is False
    assert (tmp_path/'families'/'NOISE.json').exists()


def test_meta_labeling_selects_calls_the_clue_predicts():
    panel = synthetic_hourly(n=24*365*4)
    future = np.sign(panel.close.shift(-12)/panel.close-1).fillna(0)
    side = pd.Series(1, index=panel.index)
    panel['clue'] = future+np.random.default_rng(2).normal(0, .8, len(panel))   # informative, noisy feature
    features = panel[['clue']]
    result, _ = sr.meta_primary(panel, 'intraday', side, 12, features, configs=[('logit', 'expanding', .25)])
    unfiltered = selective.report(sr.score_config(panel[panel.index < DEV_END], 'intraday', side, 12), len(panel))
    assert result['report']['hit_rate'] > unfiltered['hit_rate']+.1


def test_holdout_runs_once(tmp_path):
    report = dict(passing=[], frozen={})
    first = sr.holdout(report, panels={}, output=tmp_path)
    assert first['evaluated'] == []
    with pytest.raises(RuntimeError, match='already'):
        sr.holdout(report, panels={}, output=tmp_path)
    assert json.loads((tmp_path/'holdout.lock').read_text())['evaluated'] == []
