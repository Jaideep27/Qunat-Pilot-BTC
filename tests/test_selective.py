"""Selective-call scoring: triple barrier, costs, funding, one position at a time, and the pass rule."""
import json

import numpy as np
import pandas as pd
import pytest

from quantpilot import selective


def bars(closes, highs=None, lows=None, start='2024-01-01 01:00'):
    index = pd.date_range(start, periods=len(closes), freq='h', tz='UTC')
    closes = np.asarray(closes, float)
    return pd.DataFrame(dict(close=closes, high=closes if highs is None else highs,
                             low=closes if lows is None else lows), index=index)


def side(frame, at, value=1):
    s = pd.Series(0, index=frame.index)
    for i in np.atleast_1d(at):
        s.iloc[i] = value
    return s


def test_long_take_profit_is_hit_at_the_barrier_price():
    frame = bars([100, 100.5, 100.9, 100], highs=[100, 100.5, 102.5, 100], lows=[100, 99.6, 100.4, 99])
    calls = selective.simulate(frame, side(frame, 0), horizon=3, width=pd.Series(.02, index=frame.index))
    call = calls.iloc[0]
    assert call.reason == 'tp' and call.exit == pytest.approx(102.0)
    assert call.net == pytest.approx(.02-selective.ROUND_TRIP)
    assert call.hit == 1


def test_a_bar_touching_both_barriers_counts_as_the_stop():
    frame = bars([100, 100, 100], highs=[100, 103, 100], lows=[100, 97, 100])
    call = selective.simulate(frame, side(frame, 0), horizon=2, width=pd.Series(.02, index=frame.index)).iloc[0]
    assert call.reason == 'sl' and call.exit == pytest.approx(98.0) and call.hit == 0


def test_short_calls_profit_from_falls_and_time_exits_use_the_last_close():
    frame = bars([100, 99.5, 99.0, 99.2])
    call = selective.simulate(frame, side(frame, 0, -1), horizon=3, width=pd.Series(.05, index=frame.index)).iloc[0]
    assert call.reason == 'time' and call.exit == pytest.approx(99.2)
    assert call.gross == pytest.approx(.008) and call.net == pytest.approx(.008-selective.ROUND_TRIP)


def test_signals_while_a_position_is_open_are_ignored():
    frame = bars([100]*10)
    calls = selective.simulate(frame, side(frame, [0, 1, 2, 3, 4]), horizon=3, width=pd.Series(.05, index=frame.index))
    assert list(calls.entry_time) == [frame.index[0], frame.index[3]]


def test_longs_pay_positive_funding_and_shorts_receive_it():
    frame = bars([100]*4)
    funding = pd.Series([0, .001, 0, .002], index=frame.index)
    width = pd.Series(.05, index=frame.index)
    long = selective.simulate(frame, side(frame, 0), horizon=3, width=width, funding=funding).iloc[0]
    short = selective.simulate(frame, side(frame, 0, -1), horizon=3, width=width, funding=funding).iloc[0]
    assert long.funding == pytest.approx(.003) and long.net == pytest.approx(-selective.ROUND_TRIP-.003)
    assert short.net == pytest.approx(-selective.ROUND_TRIP+.003)


def test_calls_without_a_full_future_path_are_not_scored():
    frame = bars([100, 101, np.nan, 102, 103])
    calls = selective.simulate(frame, side(frame, [0, 3]), horizon=3, width=pd.Series(.05, index=frame.index))
    assert calls.empty


def test_hourly_funding_charges_only_settlement_hours():
    index = pd.date_range('2024-01-01 06:00', periods=4, freq='h', tz='UTC')
    rates = pd.Series(.0002, index=index)
    assert selective.settlement_funding(rates).tolist() == [0, 0, .0002, 0]  # 08:00 only


def test_wilson_break_even_and_profit_factor_in_the_report():
    calls = pd.DataFrame(dict(entry_time=pd.date_range('2024-01-01', periods=10, freq='D', tz='UTC'),
                              net=[.02]*6+[-.01]*4))
    calls['hit'] = (calls.net > 0).astype(int)
    report = selective.report(calls, bars=100)
    assert report['calls'] == 10 and report['coverage'] == pytest.approx(.1)
    assert report['hit_rate'] == pytest.approx(.6)
    assert report['break_even'] == pytest.approx(.01/(.02+.01))
    assert report['profit_factor'] == pytest.approx(.12/.04)
    assert 0 < report['wilson_low'] < .6 < report['wilson_high'] < 1


def test_deflated_sharpe_falls_as_more_trials_are_tried():
    one = selective.deflated_sharpe(.2, 200, 0, 3, trials=1, sharpe_variance=.01)
    many = selective.deflated_sharpe(.2, 200, 0, 3, trials=500, sharpe_variance=.01)
    assert one > .99 and many < one


def test_trial_registry_counts_each_configuration_once(tmp_path):
    path = tmp_path/'trials.jsonl'
    for _ in range(2):
        selective.register(path, 'swing', 'S1', dict(L=20, H=7), dict(sharpe=.1))
    selective.register(path, 'swing', 'S1', dict(L=55, H=7), dict(sharpe=.2))
    selective.register(path, 'intraday', 'I1', dict(x=.02), dict(sharpe=.3))
    assert selective.trial_count(path, 'swing') == 2
    assert selective.trial_sharpes(path, 'swing') == [.1, .2]
    assert len(path.read_text().splitlines()) == 4 and json.loads(path.read_text().splitlines()[0])['family'] == 'S1'


def random_walk(n=24*365*2, seed=3):
    rng = np.random.default_rng(seed)
    close = 30000*np.exp(np.cumsum(rng.normal(0, .006, n)))
    noise = np.abs(rng.normal(0, .002, n))
    return bars(close, highs=close*(1+noise), lows=close*(1-noise))


def evaluate(frame, signal, horizon=12):
    width = selective.barrier_width(frame.close, window=168, horizon=horizon)
    calls = selective.simulate(frame, signal, horizon=horizon, width=width)
    rep = selective.report(calls, bars=len(frame))
    return selective.gate(rep, track='intraday', trials=1, sharpe_variance=0.0, pbo=0.0)


def test_leakage_canary_future_peeking_signal_passes_the_gate():
    frame = random_walk()
    peek = np.sign(frame.close.shift(-12)/frame.close-1).fillna(0)
    assert evaluate(frame, peek)['passed'] is True


def test_random_and_lagged_signals_fail_the_gate():
    frame = random_walk()
    rng = np.random.default_rng(9)
    noise = pd.Series(rng.choice([-1, 0, 0, 0, 1], len(frame)), index=frame.index)
    lagged = np.sign(frame.close/frame.close.shift(24)-1).fillna(0)
    assert evaluate(frame, noise)['passed'] is False
    assert evaluate(frame, lagged)['passed'] is False


def test_gate_reports_each_criterion():
    calls = pd.DataFrame(dict(entry_time=pd.date_range('2020-01-01', periods=120, freq='7D', tz='UTC'),
                              net=[.03, -.01]*60))
    calls['hit'] = (calls.net > 0).astype(int)
    result = selective.gate(selective.report(calls, bars=1000), track='swing', trials=10, sharpe_variance=.001, pbo=.5)
    assert result['criteria']['enough_calls'] and result['criteria']['beats_break_even']
    assert result['criteria']['low_overfitting'] is False and result['passed'] is False


def test_overlap_mode_labels_every_signal_bar():
    frame = bars([100]*10)
    calls = selective.simulate(frame, side(frame, [0, 1, 2]), horizon=3, width=pd.Series(.05, index=frame.index),
                               overlap=True)
    assert list(calls.entry_time) == list(frame.index[:3])
