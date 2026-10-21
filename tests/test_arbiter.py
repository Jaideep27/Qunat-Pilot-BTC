"""The arbiter: gates, layer modes, selection order, flips and position management."""
import copy

import pandas as pd
import pytest

from quantpilot import arbiter, rules, triggers

T = pd.Timestamp('2026-03-03 10:15', tz='UTC')


def book():
    b = copy.deepcopy(rules.load())
    simple = dict(trigger={'any': ['LEVEL_SWEEP_LOW'], 'within_bars': 0, 'min_bars': 0}, regimes=['RANGE'],
                  conditions=[['x', '>', 0]], stop={'anchor': 'close', 'buffer_atr': 1.0},
                  target={'candidates': [], 'cap': None, 'fallback_r': 2.0}, abort=[['close_15m', '<', 'position.anchor']],
                  time_stop_hours=12, breakeven_at_r=1.0, may_flip=False, family='test', source=[], thesis='')
    b['playbooks'] = [dict(simple, id='A', name='A', side='long', priority=2),
                      dict(simple, id='B', name='B', side='long', priority=1),
                      dict(simple, id='C', name='C', side='short', priority=3, may_flip=True,
                           stop={'anchor': 'close', 'buffer_atr': 1.0}),
                      dict(simple, id='S', name='S', side='long', priority=0, shadow_only=True)]
    return b


def frame():
    index = pd.date_range(T-pd.Timedelta(hours=2), T, freq='5min')
    pf = pd.DataFrame(index=index)
    for k, v in dict(close=100.0, high=100.2, low=99.8, atr_1h=1.0, x=1.0, adx_1h=15.0, atr_pct_1h=.5, ema_stack_1h=0.0,
                     ret_4h=0.0, sweep_low=1.0, open_interest=1000.0, support=99.0, resistance=101.0, move_z_15m=0.0,
                     funding_rate=0.0, close_15m=100.0).items():
        pf[k] = v
    return pf


def decide(pf, b=None, account=None, **kw):
    b = b or book()
    table = triggers.detect(pf, b)
    return arbiter.decide(b, T, pf, triggers.TriggerIndex(table), account or dict(equity=10_000.0, cash=10_000.0),
                          **kw)


def test_highest_priority_wins_and_the_rest_are_recorded():
    out = decide(frame())
    assert out['action'] == 'enter' and out['chosen']['playbook'] == 'B'
    status = {h['playbook']: h['status'] for h in out['hits']}
    assert status == {'A': 'SKIPPED_LOWER_PRIORITY', 'B': 'TAKEN', 'C': 'SKIPPED_LOWER_PRIORITY', 'S': 'SHADOW'}


def test_cascade_halt_blocks_new_entries_for_30_minutes():
    pf = frame()
    pf.loc[T-pd.Timedelta(minutes=20), 'move_z_15m'] = -9.0
    out = decide(pf)
    assert out['action'] is None and 'cascade_halt' in out['gates']
    pf2 = frame()
    pf2.loc[T-pd.Timedelta(minutes=40), 'move_z_15m'] = -9.0
    assert decide(pf2)['action'] == 'enter'


def test_funding_window_and_cooldown():
    pf = frame()
    funding_time = T.floor('8h')+pd.Timedelta(hours=8)
    gates = arbiter.gates(book(), funding_time, pf.iloc[-1].to_dict(), pf, history=[], account={})
    assert 'funding_window' in gates
    history = [dict(playbook='B', exit_time=T-pd.Timedelta(hours=h), net=-.01) for h in (3, 2)]
    out = decide(pf, history=history)
    assert out['chosen']['playbook'] == 'A'
    assert {h['playbook']: h['status'] for h in out['hits']}['B'] == 'COOLDOWN'


def test_a_live_layer_can_veto_but_a_shadow_layer_only_records():
    veto = {'ml': lambda hit, features: dict(verdict='skip', p=.2)}
    shadow = decide(frame(), layers=veto)
    assert shadow['chosen']['playbook'] == 'B' and shadow['chosen']['layers']['ml']['verdict'] == 'skip'
    live = decide(frame(), layers=veto, modes={('ml', k): 'live' for k in 'ABC'})
    status = {h['playbook']: h['status'] for h in live['hits']}
    assert status['B'] == 'VETO_ML' and status['A'] == 'VETO_ML' and live['action'] is None


def test_a_live_learner_can_widen_the_stop():
    wide = {'learner': lambda hit, features: dict(verdict='take_wide')}
    out = decide(frame(), layers=wide, modes={('learner', 'B'): 'live'})
    plan = out['chosen']['plan']
    assert plan['stop'] == pytest.approx(100.0-1.67) and plan['target'] == pytest.approx(100.0+2*1.67)
    assert out['chosen']['action'] == 'take_wide'


def test_an_open_position_blocks_entries_unless_a_flip_playbook_fires_against_it():
    pf = frame()
    account = dict(equity=10_000.0, cash=10_000.0, position=dict(side=-1, playbook='X'))
    out = decide(pf, account=account)
    assert out['action'] is None
    b = book()
    b['playbooks'][2]['side'] = 'long'
    b['playbooks'][2]['priority'] = 0
    flip = decide(pf, b=b, account=account)
    assert flip['action'] == 'flip' and flip['chosen']['playbook'] == 'C'


def test_manage_moves_the_stop_to_breakeven_and_aborts():
    b = book()
    pb = rules.playbook(b, 'B')
    position = dict(side=1, entry=100.0, stop=98.0, initial_stop=98.0, max_favorable=.021, anchor=99.0, playbook='B',
                    entry_time=(T-pd.Timedelta(hours=1)).isoformat(), breakeven=False, open_interest=1000.0)
    act = arbiter.manage(pb, position, dict(close_15m=100.5, open_interest=1000.0), T, b['defaults'])
    assert act['action'] == 'tighten_stop' and act['price'] == pytest.approx(100.0*(1+arbiter.ROUND_TRIP))
    act = arbiter.manage(pb, position, dict(close_15m=98.9, open_interest=1000.0), T, b['defaults'])
    assert act['action'] == 'exit' and act['reason'] == 'ABORT'
    late = dict(position, entry_time=(T-pd.Timedelta(hours=13)).isoformat())
    assert arbiter.manage(pb, late, dict(close_15m=100.5), T, b['defaults'])['reason'] == 'TIME_STOP'
