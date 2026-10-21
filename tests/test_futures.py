"""Versioned futures simulator (futures-v1): one pure reducer for history and the forward paper account."""
import json

import pandas as pd
import pytest

from quantpilot import futures

T0 = pd.Timestamp('2026-01-20 10:00', tz='UTC')
SLIP, FEE = futures.SLIPPAGE, futures.FEE


def bar(i, o, h, l, c, mark=None):
    return dict(kind='bar', time=(T0+pd.Timedelta(minutes=5*i)).isoformat(), open=o, high=h, low=l, close=c,
                mark=mark if mark is not None else c)


def enter(i, side='long', qty=.01, stop=99_000.0, target=103_000.0, cost=futures.ROUND_TRIP):
    plan = dict(side=side, quantity=qty, stop=stop, target=target, leverage=2.0, cost_fraction=cost)
    return dict(kind='action', time=(T0+pd.Timedelta(minutes=5*i)).isoformat(), action='enter', plan=plan)


def act(i, action, price=None):
    return dict(kind='action', time=(T0+pd.Timedelta(minutes=5*i)).isoformat(), action=action, price=price)


def run(events, balance=10_000.0):
    return futures.replay(futures.initial(balance), events)


FLAT = [bar(0, 100_000, 100_050, 99_950, 100_000)]


def test_entries_fill_at_the_next_open_with_slippage_never_at_the_decision_close():
    state = run(FLAT+[enter(0), bar(1, 100_200, 100_300, 100_100, 100_250)])
    p = state['position']
    assert p['entry'] == pytest.approx(100_200*(1+SLIP)) and p['entry_time'] == FLAT[0]['time'].replace('10:00', '10:05')
    assert state['cash'] == pytest.approx(10_000-.01*p['entry']*FEE)


def test_a_gap_through_the_stop_fills_at_the_open():
    state = run(FLAT+[enter(0), bar(1, 100_000, 100_100, 99_900, 100_000), bar(2, 98_500, 98_700, 98_400, 98_600)])
    trade = state['trades'][-1]
    assert trade['reason'] == 'STOP' and trade['exit'] == pytest.approx(98_500*(1-SLIP))


def test_a_bar_touching_both_stop_and_target_counts_as_the_stop():
    state = run(FLAT+[enter(0), bar(1, 100_000, 103_500, 98_900, 101_000)])
    assert state['trades'][-1]['reason'] == 'STOP'


def test_targets_fill_at_the_target_price():
    state = run(FLAT+[enter(0), bar(1, 100_000, 100_100, 99_900, 100_000), bar(2, 101_000, 103_200, 100_900, 102_000)])
    trade = state['trades'][-1]
    assert trade['reason'] == 'TARGET' and trade['exit'] == pytest.approx(103_000*(1-SLIP))


def test_short_positions_mirror_and_receive_positive_funding():
    events = FLAT+[enter(0, 'short', stop=101_000.0, target=97_000.0), bar(1, 100_000, 100_100, 99_900, 100_000),
                   dict(kind='funding', time=(T0+pd.Timedelta(minutes=5)).isoformat(), rate=.0001, mark=100_000.0)]
    state = run(events)
    assert state['position']['funding'] == pytest.approx(.01*100_000*.0001)    # received
    long = run(FLAT+[enter(0), bar(1, 100_000, 100_100, 99_900, 100_000),
                     dict(kind='funding', time=(T0+pd.Timedelta(minutes=5)).isoformat(), rate=.0001, mark=100_000.0)])
    assert long['position']['funding'] == pytest.approx(-.01*100_000*.0001)    # paid


def test_liquidation_uses_the_mark_and_loses_the_margin():
    plan_stop = 40_000.0                                       # deliberately beyond liquidation (tests the cap)
    events = FLAT+[enter(0, stop=plan_stop, target=300_000.0, qty=.05), bar(1, 100_000, 100_100, 99_900, 100_000),
                   bar(2, 60_000, 60_100, 45_000, 50_000, mark=49_000)]
    state = run(events)
    trade = state['trades'][-1]
    margin = .05*100_000*(1+SLIP)/2
    assert trade['reason'] == 'LIQUIDATION' and trade['pnl'] == pytest.approx(-margin)


def test_stops_only_tighten_and_targets_only_move_closer():
    base = FLAT+[enter(0), bar(1, 100_000, 100_100, 99_900, 100_000)]
    tightened = run(base+[act(1, 'tighten_stop', 99_500.0), bar(2, 100_000, 100_100, 99_900, 100_000)])
    assert tightened['position']['stop'] == 99_500.0
    widened = run(base+[act(1, 'tighten_stop', 98_000.0), bar(2, 100_000, 100_100, 99_900, 100_000)])
    assert widened['position']['stop'] == 99_000.0 and widened['rejections'][-1]['reason'] == 'INVALID_STOP_CHANGE'
    past_price = run(base+[act(1, 'tighten_stop', 100_500.0), bar(2, 100_000, 100_100, 99_900, 100_000)])
    assert past_price['position']['stop'] == 99_000.0
    closer = run(base+[act(1, 'reduce_target', 102_000.0), bar(2, 100_000, 100_100, 99_900, 100_000)])
    assert closer['position']['target'] == 102_000.0
    farther = run(base+[act(1, 'reduce_target', 104_000.0), bar(2, 100_000, 100_100, 99_900, 100_000)])
    assert farther['position']['target'] == 103_000.0


def test_no_adding_reversing_or_entering_while_a_position_is_open():
    state = run(FLAT+[enter(0), bar(1, 100_000, 100_100, 99_900, 100_000), enter(1, 'short', stop=101_000.0, target=97_000.0),
                      bar(2, 100_000, 100_100, 99_900, 100_000)])
    assert state['position']['side'] == 1 and state['rejections'][-1]['reason'] == 'POSITION_OPEN'


def test_entries_invalidated_by_the_open_are_rejected_not_filled():
    state = run(FLAT+[enter(0), bar(1, 102_800, 102_900, 102_700, 102_850)])   # most of the reward is gone
    assert state['position'] is None and state['rejections'][-1]['reason'] == 'INVALIDATED_AT_FILL'


def test_entries_need_margin_within_available_cash():
    state = run(FLAT+[enter(0, qty=.5, stop=95_000.0, target=110_000.0), bar(1, 100_000, 100_100, 99_900, 100_000)])
    assert state['position'] is None and state['rejections'][-1]['reason'] == 'INSUFFICIENT_MARGIN'


def test_positions_close_after_24_hours():
    events = FLAT+[enter(0)]+[bar(i, 100_000, 100_050, 99_950, 100_000) for i in range(1, 290)]
    state = run(events)
    assert state['trades'][-1]['reason'] == 'TIME_EXIT'
    held = pd.Timestamp(state['trades'][-1]['exit_time'])-pd.Timestamp(state['trades'][-1]['entry_time'])
    assert held == pd.Timedelta(hours=24)


def test_a_2_percent_daily_loss_closes_and_halts_entries_until_the_next_day():
    events = FLAT+[enter(0, qty=.19, stop=95_000.0, target=110_000.0), bar(1, 100_000, 100_100, 99_900, 100_000),
                   bar(2, 99_000, 99_100, 98_800, 98_900)]                    # about -2.2% of equity
    state = run(events, balance=10_000.0)
    assert state['trades'][-1]['reason'] == 'DAILY_LOSS_HALT' and state['halted_day'] is True
    later = futures.replay(state, [enter(2, stop=98_000.0), bar(3, 99_500, 99_600, 99_400, 99_500)])
    assert later['position'] is None and later['rejections'][-1]['reason'] == 'ACCOUNT_HALTED'
    tomorrow = pd.Timestamp('2026-01-21 00:05', tz='UTC')
    resumed = futures.replay(later, [dict(kind='bar', time=tomorrow.isoformat(), open=99_500, high=99_600, low=99_400,
                                          close=99_500, mark=99_500)])
    assert resumed['halted_day'] is False


def test_a_10_percent_drawdown_halts_until_owner_review():
    state = futures.initial(10_000.0)
    state['peak'], state['cash'] = 10_000.0, 8_900.0                          # already 11% below the peak
    state = futures.replay(state, FLAT)
    assert state['drawdown_halt'] is True
    reviewed = futures.replay(state, [dict(kind='control', time=T0.isoformat(), review=True)])
    assert reviewed['drawdown_halt'] is False and reviewed['peak'] == pytest.approx(8_900.0)


def test_replay_in_one_pass_equals_resuming_from_a_saved_state():
    events = FLAT+[enter(0), bar(1, 100_000, 100_300, 99_900, 100_200), act(1, 'tighten_stop', 99_600.0),
                   bar(2, 100_200, 100_400, 100_100, 100_300), act(2, 'exit'), bar(3, 100_350, 100_400, 100_300, 100_380)]
    whole = run(events)
    first = run(events[:4])
    resumed = futures.replay(json.loads(json.dumps(first)), events[4:])
    assert resumed == whole and whole['trades'][-1]['reason'] == 'EXIT_ACTION'
    assert whole['version'] == futures.VERSION


def test_out_of_order_bars_are_rejected():
    with pytest.raises(ValueError):
        run([bar(1, 1, 1, 1, 1), bar(0, 1, 1, 1, 1)])


def test_trade_records_carry_costs_excursions_and_protection_changes():
    events = FLAT+[enter(0), bar(1, 100_000, 100_600, 99_400, 100_200), act(1, 'tighten_stop', 99_600.0),
                   bar(2, 100_200, 100_300, 99_500, 99_550)]
    trade = run(events)['trades'][-1]
    assert trade['reason'] == 'STOP' and trade['fees'] > 0
    assert trade['max_favorable'] == pytest.approx(100_600/trade['entry']-1)
    assert trade['max_adverse'] == pytest.approx(99_400/trade['entry']-1)
    assert trade['protection_changes'][0]['stop'] == 99_600.0
    assert trade['net'] == pytest.approx(trade['pnl']-trade['fees']+trade['funding'])


def test_fill_revalidation_uses_the_plans_own_minimum_reward_risk():
    strict = enter(0)
    strict['plan']['min_reward_risk'] = 2.0
    # At a 100,400 fill: (103,000-100,430)/100,430-0.0016 vs (100,430-99,000)/100,430+0.0016 -> about 1.5-1.6
    state = run(FLAT+[strict, bar(1, 100_400, 100_500, 100_300, 100_450)])
    assert state['position'] is None and state['rejections'][-1]['reason'] == 'INVALIDATED_AT_FILL'
    assert run(FLAT+[enter(0), bar(1, 100_400, 100_500, 100_300, 100_450)])['position'] is not None


def test_a_plan_can_carry_stressed_slippage_for_both_fills():
    e = enter(0)
    e['plan']['slippage'] = 3*SLIP
    state = run(FLAT+[e, bar(1, 100_000, 100_100, 99_900, 100_000), bar(2, 100_000, 103_100, 99_950, 103_000)])
    t = state['trades'][0]
    assert t['entry'] == pytest.approx(100_000*(1+3*SLIP)) and t['exit'] == pytest.approx(103_000*(1-3*SLIP))
