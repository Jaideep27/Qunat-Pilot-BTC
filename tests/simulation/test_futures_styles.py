"""futures-v1 additions for the A10 styles: resting limit entries, longer holds and stop-only trades.
Old plans carry none of the new fields, so their behaviour is unchanged (tests/simulation/test_futures.py)."""
import pandas as pd
import pytest

from quantpilot.simulation import futures

T0 = pd.Timestamp('2026-01-20 10:00', tz='UTC')


def at(i):
    return (T0+pd.Timedelta(minutes=5*i)).isoformat()


def bar(i, o, h, l, c):
    return dict(kind='bar', time=at(i), open=o, high=h, low=l, close=c, mark=c)


def enter(i, **plan):
    base = dict(side='long', quantity=.01, stop=99_000.0, target=103_000.0, leverage=2.0, cost_fraction=futures.ROUND_TRIP,
                min_reward_risk=None)
    return dict(kind='action', time=at(i), action='enter', plan=dict(base, **plan))


def run(events):
    return futures.replay(futures.initial(10_000.0), events)


def test_a_limit_entry_rests_until_the_price_reaches_it_and_fills_at_the_limit():
    s = run([bar(0, 100_500, 100_600, 100_400, 100_500), enter(0, limit=100_000.0, expires=at(4)),
             bar(1, 100_500, 100_600, 100_300, 100_400), bar(2, 100_400, 100_450, 99_900, 100_100)])
    p = s['position']
    assert p is not None and p['entry'] == pytest.approx(100_000.0) and p['entry_time'] == at(2)


def test_a_limit_below_a_gap_down_fills_at_the_better_open():
    s = run([bar(0, 100_500, 100_600, 100_400, 100_500), enter(0, limit=100_000.0, expires=at(4)),
             bar(1, 99_800, 99_900, 99_700, 99_850)])
    assert s['position']['entry'] == pytest.approx(99_800.0)


def test_an_unfilled_limit_expires_with_a_rejection():
    s = run([bar(0, 100_500, 100_600, 100_400, 100_500), enter(0, limit=100_000.0, expires=at(2))]
            + [bar(i, 100_500, 100_600, 100_400, 100_500) for i in range(1, 5)])
    assert s['position'] is None and s['pending'] is None
    assert s['rejections'][-1]['reason'] == 'LIMIT_EXPIRED'


def test_a_short_limit_fills_when_price_rises_to_it():
    s = run([bar(0, 100_000, 100_100, 99_900, 100_000),
             enter(0, side='short', limit=100_400.0, expires=at(4), stop=101_500.0, target=99_000.0),
             bar(1, 100_000, 100_500, 99_950, 100_300)])
    assert s['position']['side'] == -1 and s['position']['entry'] == pytest.approx(100_400.0)


def test_max_hold_hours_replaces_the_24_hour_time_exit():
    events = [bar(0, 100_000, 100_050, 99_950, 100_000), enter(0, max_hold_hours=48)]
    events += [bar(i, 100_000, 100_050, 99_950, 100_000) for i in range(1, 12*30)]   # 30 hours
    s = run(events)
    assert s['position'] is not None
    events += [bar(i, 100_000, 100_050, 99_950, 100_000) for i in range(12*30, 12*49)]
    s = run(events)
    assert s['position'] is None and s['trades'][-1]['reason'] == 'TIME_EXIT'


def test_a_stop_only_trade_has_no_target_and_still_stops():
    events = [bar(0, 100_000, 100_050, 99_950, 100_000), enter(0, target=None, max_hold_hours=24*30),
              bar(1, 100_000, 110_000, 99_950, 109_000)]
    s = run(events)
    assert s['position'] is not None and s['position']['target'] is None
    s = run(events+[bar(2, 109_000, 109_100, 98_000, 98_500)])
    assert s['trades'][-1]['reason'] == 'STOP' and s['trades'][-1]['exit'] == pytest.approx(99_000.0*(1-futures.SLIPPAGE))


def test_style_fields_are_kept_on_the_trade():
    s = run([bar(0, 100_000, 100_050, 99_950, 100_000), enter(0, style='R1', hit_id='R1:x', max_hold_hours=4),
             bar(1, 100_000, 103_500, 99_950, 103_000)])
    assert s['trades'][-1]['plan']['style'] == 'R1' and s['trades'][-1]['plan']['max_hold_hours'] == 4
