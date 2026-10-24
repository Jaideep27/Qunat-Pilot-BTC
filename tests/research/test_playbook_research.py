"""Historical playbook research (amendment A6) on a small synthetic market."""
import copy
import json

import pandas as pd
import pytest

from agent_fixtures import frames
from quantpilot.research import playbooks as playbook_research
from quantpilot.rules import rulebook as rules

START = pd.Timestamp('2025-01-06', tz='UTC')   # inside the development span (before 2025-10-20)


def book():
    b = copy.deepcopy(rules.load())
    b['triggers']['EVERY_BAR'] = dict(direction=0, at='15m', when=['close', '>', 0])
    common = dict(family='test', source=[], thesis='', regimes=['RANGE', 'TREND_UP', 'TREND_DOWN', 'HIGH_VOL', 'PANIC'],
                  trigger={'any': ['EVERY_BAR'], 'within_bars': 0, 'min_bars': 0}, conditions=[['up_15m', '==', 1]],
                  stop={'anchor': 'close', 'buffer_atr': 1.0}, target={'candidates': [], 'cap': None, 'fallback_r': 3.0},
                  abort=[['close_15m', '<', 'position.anchor']], time_stop_hours=6, breakeven_at_r=1.0, may_flip=False)
    b['playbooks'] = [dict(common, id='T1', name='T1', side='long', priority=1),
                      dict(common, id='T2', name='T2', side='short', priority=2, conditions=[['up_15m', '==', 0]],
                           may_flip=True)]
    b['hash'] = 'testbook'
    return b


@pytest.fixture(scope='module')
def run(tmp_path_factory):
    data = frames(days=50, start=START)
    out = tmp_path_factory.mktemp('re')
    start, end = START+pd.Timedelta(days=25), START+pd.Timedelta(days=45)
    report = playbook_research.research(out, frames=data, macro_events=[], book=book(), start=start, end=end,
                                        chunk_days=10, priors=())
    return out, report, start, end


def test_hits_carry_plans_and_both_counterfactual_outcomes(run):
    out, report, start, end = run
    hits = playbook_research.load_hits(out)
    assert set(hits.playbook) == {'T1', 'T2'} and len(hits) > 200
    assert hits.time.min() >= start and hits.time.max() < end
    tradeable = hits[hits.tradeable]
    assert tradeable.o_reason.notna().all() and tradeable.w_reason.notna().all()
    assert (tradeable.w_risk_fraction > tradeable.o_risk_fraction).mean() > .95
    assert pd.to_datetime(tradeable.o_exit_time, utc=True).max() <= end
    assert {'f_move_z_15m', 'f_range_pos_4h', 'confluence'} <= set(hits.columns)


def test_each_playbook_is_scored_one_position_at_a_time(run):
    _, report, _, _ = run
    for pid in ('T1', 'T2'):
        result = report['playbooks'][pid]
        assert result['report']['calls'] > 10 and set(result['gate']['criteria']) >= {'enough_calls', 'positive_mean'}
        trades = pd.DataFrame(result['trades'])
        assert (pd.to_datetime(trades.entry_time).iloc[1:].to_numpy() >=
                pd.to_datetime(trades.exit_time).iloc[:-1].to_numpy()).all()


def test_the_combined_account_trades_one_position_at_a_time(run):
    _, report, _, _ = run
    combined = report['combined']
    trades = combined['trades']
    assert combined['account']['trades'] == len(trades) > 5
    assert {t['plan']['playbook'] for t in trades} <= {'T1', 'T2'}
    for a, b in zip(trades, trades[1:]):
        assert pd.Timestamp(b['entry_time']) >= pd.Timestamp(a['exit_time'])


def test_trials_are_registered_once_and_the_run_resumes(run):
    out, report, start, end = run
    lines = (out/'trials.jsonl').read_text(encoding='utf-8').splitlines()
    families = [json.loads(line)['config']['playbook'] for line in lines if json.loads(line)['family'].startswith('RE')]
    assert sorted(families) == ['RE-combined', 'T1', 'T2']
    stamps = {p.name: p.stat().st_mtime for p in (out/'hits').glob('*.parquet')}
    again = playbook_research.research(out, frames=frames(days=50, start=START), macro_events=[], book=book(), start=start, end=end,
                                       chunk_days=10, priors=())
    assert {p.name: p.stat().st_mtime for p in (out/'hits').glob('*.parquet')} == stamps
    assert again['playbooks']['T1']['report'] == report['playbooks']['T1']['report']
    assert len((out/'trials.jsonl').read_text(encoding='utf-8').splitlines()) == len(lines)
