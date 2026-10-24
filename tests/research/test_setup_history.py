"""'What happened the last times this setup appeared': per-playbook history from a research run."""
import numpy as np
import pandas as pd
import pytest

from quantpilot.research import setup_history


@pytest.fixture
def run(tmp_path):
    t = pd.date_range('2024-01-01', periods=2000, freq='5min', tz='UTC')
    close = 100+np.arange(2000)*.01                         # a steady rise: longs always "went their way"
    (tmp_path/'bars').mkdir()
    (tmp_path/'hits').mkdir()
    pd.DataFrame(dict(open=close, high=close+.05, low=close-.05, close=close, mark=close), index=t).to_parquet(
        tmp_path/'bars'/'20240101.parquet')
    times = t[[100, 300, 500, 700, 900]]
    pd.DataFrame(dict(id=[f'PB09L:{x}' for x in times], playbook='PB09L', side='long', time=times, priority=2,
                      tradeable=[True, True, False, True, False], o_valid=True, o_net=[.01, -.005, .002, .008, -.001],
                      o_r=[1.2, -1.05, .3, .9, -.1])).to_parquet(tmp_path/'hits'/'20240101.parquet')
    return tmp_path


def test_history_counts_direction_and_simulated_plan_results(run, tmp_path):
    out = setup_history.build(run)
    pb = out['PB09L']
    assert pb['setups'] == 5 and pb['went_their_way_1h']['share'] == 1.0 and pb['went_their_way_4h']['share'] == 1.0
    assert pb['plan']['n'] == 5 and pb['plan']['win_rate'] == pytest.approx(.6) and pb['plan']['mean_r'] == pytest.approx(.25)
    assert 0 < pb['went_their_way_4h']['low'] < 1 and pb['median_move_4h'] > 0
    path = setup_history.write(run, tmp_path/'history.json')
    assert setup_history.lookup('PB09L', path)['setups'] == 5 and setup_history.lookup('PB99', path) is None
