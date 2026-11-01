"""The Combined Bot's single book (A11): one price trade at a time, idle money in the hedge, cash conserved."""
import pandas as pd
import pytest

from quantpilot.simulation import carry as ledger
from quantpilot.strategies import bot

T0 = pd.Timestamp('2026-03-02', tz='UTC')


def at(minutes):
    return T0+pd.Timedelta(minutes=minutes)


def bars(st, start, n, price=100_000.0, step=5):
    for k in range(n):
        t = at(start+k*step)
        bot.on_bar(st, t, price, price*1.0005, price*.9995, price)
    return at(start+(n-1)*step)


def plan(side='long', price=100_000.0):
    return dict(side=side, quantity=.05, stop=price*.99 if side == 'long' else price*1.01,
                target=price*1.02 if side == 'long' else price*.98, leverage=2.0, cost_fraction=.0016, min_reward_risk=None,
                max_hold_hours=12, style='X1', playbook='PB08L', hit_id='X1:test')


def test_idle_money_parks_after_a_flat_day_and_the_hedge_closes_before_a_trade():
    st = bot.new(10_000.0, now=T0, bar_minutes=5)
    t = bars(st, 5, 12*25)                                      # 25 flat hours
    assert bot.on_settlement(st, t, .0001, 100_000.0, [.0001]*21) == 'parked'
    assert st['hedge']['open'] and bot.equity(st, 100_000.0) == pytest.approx(10_000.0, abs=30)
    for k in range(6):
        bot.on_settlement(st, t, .0001, 100_000.0, [.0001]*21)
    funded = st['hedge']['cycle']['funding']
    assert funded > 0
    before = bot.equity(st, 100_000.0)
    bot.enter(st, plan(), 100_000.0, t)
    assert st['hedge'] is None and st['futures']['pending']['action'] == 'enter'
    assert len(st['cycles']) == 1 and st['cycles'][0]['reason'] == 'TRADE_SIGNAL'
    assert st['futures']['equity'] == pytest.approx(10_000.0+st['cycles'][0]['net'])   # the hedge's result is handed back
    assert before-st['futures']['equity'] == pytest.approx(st['cycles'][0]['fees']/2, rel=.05)   # only the closing costs differ
    bars(st, 25*60+10, 2)
    assert st['futures']['position'] is not None


def test_no_parking_before_a_flat_day_or_with_negative_funding_or_when_switched_off():
    st = bot.new(10_000.0, now=T0, bar_minutes=5)
    t = bars(st, 5, 12*10)
    assert bot.on_settlement(st, t, .0001, 100_000.0, [.0001]*21) is None
    t = bars(st, 12*10*5+5, 12*20)
    assert bot.on_settlement(st, t, -.0001, 100_000.0, [-.0001]*21) is None
    assert bot.on_settlement(st, t, .0001, 100_000.0, [.0001]*21, allow_park=False) is None


def test_the_hedge_closes_when_funding_turns():
    st = bot.new(10_000.0, now=T0, bar_minutes=5)
    t = bars(st, 5, 12*25)
    bot.on_settlement(st, t, .0001, 100_000.0, [.0001]*21)
    assert bot.on_settlement(st, t, -.0003, 100_000.0, [.0001]*12+[-.0003]*9) == 'unparked'
    assert st['hedge'] is None and st['cycles'][-1]['reason'] == 'FUNDING_TURNED'


def test_a_position_resets_the_flat_clock():
    st = bot.new(10_000.0, now=T0, bar_minutes=5)
    bot.enter(st, plan(), 100_000.0, at(0))
    t = bars(st, 5, 12*6)                                       # inside the 12-hour time stop
    assert st['futures']['position'] is not None and st['flat_since'] is None
    assert bot.on_settlement(st, t, .0001, 100_000.0, [.0001]*21) is None
