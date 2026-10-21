"""Per-hit outcome simulation (the counterfactual record behind diagnostics, ML labels and the learner)."""
import numpy as np
import pandas as pd
import pytest

from quantpilot import outcomes
from quantpilot.futures import FEE, SLIPPAGE

T0 = pd.Timestamp('2026-02-02 00:05', tz='UTC')


def bars(path, n=80):
    """Bars whose open is the previous close; `path` lists (high, low, close) for bars 1.., then flat."""
    rows = [(100.2, 99.8, 100.0)]+list(path)
    rows += [(rows[-1][2]+.1, rows[-1][2]-.1, rows[-1][2])]*(n-len(rows))
    h, l, c = (np.array(x, float) for x in zip(*rows))
    o = np.r_[100.0, c[:-1]]
    t = pd.date_range(T0, periods=n, freq='5min')
    return outcomes.Bars(t=t, o=o, h=np.maximum(h, np.maximum(o, c)), l=np.minimum(l, np.minimum(o, c)), c=c)


def test_a_stop_loses_about_one_r():
    b = bars([(100.3, 99.9, 100.1), (100.0, 97.5, 98.0)])
    out = outcomes.simulate(b, 0, 'long', stop=98.5, target=104.0, max_bars=288)
    assert out['reason'] == 'STOP' and out['exit_index'] == 2
    assert out['entry'] == pytest.approx(100.0*(1+SLIPPAGE))
    assert -1.15 < out['r'] < -1.0
    assert out['net'] == pytest.approx((98.5*(1-SLIPPAGE)-out['entry'])/out['entry']-2*FEE, rel=1e-6)


def test_a_target_wins_and_an_ambiguous_bar_counts_as_the_stop():
    win = outcomes.simulate(bars([(101.0, 99.9, 100.8), (104.5, 100.5, 104.2)]), 0, 'long', 98.5, 104.0, 288)
    assert win['reason'] == 'TARGET' and win['r'] > 2.2
    both = outcomes.simulate(bars([(104.5, 98.0, 101.0)]), 0, 'long', 98.5, 104.0, 288)
    assert both['reason'] == 'STOP'


def test_a_gap_through_the_stop_fills_at_the_open():
    b = bars([(100.1, 99.9, 100.0), (100.1, 99.9, 100.0), (97.2, 95.5, 96.0)])
    b.o[3], b.h[3] = 97.0, 97.2                                  # bar 3 gaps down from 100 to 97
    out = outcomes.simulate(b, 0, 'short', stop=101.0, target=96.0, max_bars=288)
    assert out['reason'] == 'TARGET'
    long = outcomes.simulate(b, 0, 'long', stop=98.0, target=104.0, max_bars=288)
    assert long['reason'] == 'STOP' and long['exit'] == pytest.approx(97.0*(1-SLIPPAGE))


def test_breakeven_moves_the_stop_after_one_r():
    b = bars([(101.6, 99.9, 101.4), (101.5, 99.0, 99.2)])
    out = outcomes.simulate(b, 0, 'long', 98.5, 104.0, 288, breakeven_r=1.0)
    assert out['reason'] == 'BREAKEVEN_STOP' and abs(out['net']) < 5e-4 and out['breakeven_moved']
    plain = outcomes.simulate(b, 0, 'long', 98.5, 104.0, 288)
    assert plain['reason'] != 'BREAKEVEN_STOP'


def test_abort_exits_at_the_next_open_and_time_stop_at_the_close():
    b = bars([(100.3, 99.9, 100.1)]*10)
    abort = np.zeros(len(b.c), bool)
    abort[4] = True
    out = outcomes.simulate(b, 0, 'long', 98.0, 104.0, 288, abort=abort)
    assert out['reason'] == 'ABORT' and out['exit_index'] == 5 and out['exit'] == pytest.approx(b.o[5]*(1-SLIPPAGE))
    timed = outcomes.simulate(b, 0, 'long', 98.0, 104.0, max_bars=6)
    assert timed['reason'] == 'TIME' and timed['exit_index'] == 6 and timed['bars_held'] == 6


def test_funding_is_paid_by_longs_when_positive():
    b = bars([(100.3, 99.9, 100.1)]*30)
    funding = (pd.DatetimeIndex([T0+pd.Timedelta(minutes=30)]), np.array([1e-3]))
    out = outcomes.simulate(b, 0, 'long', 98.0, 104.0, max_bars=12, funding=funding)
    free = outcomes.simulate(b, 0, 'long', 98.0, 104.0, max_bars=12)
    assert out['funding'] == pytest.approx(-1e-3) and out['net'] == pytest.approx(free['net']-1e-3)


def test_excursions_and_the_post_exit_window():
    b = bars([(100.6, 99.4, 99.6), (99.7, 98.0, 98.2), (100.0, 98.2, 99.8), (104.5, 99.8, 104.2)])
    out = outcomes.simulate(b, 0, 'long', 98.4, 104.0, 288)
    assert out['reason'] == 'STOP'
    risk = out['entry']-98.4
    assert out['mfe_r'] == pytest.approx((100.6-out['entry'])/risk)
    assert out['mae_r'] == pytest.approx((98.0-out['entry'])/risk)
    assert out['target_after_exit'] and out['post_high'] >= 104.5


def test_simulation_needs_a_next_bar():
    b = bars([], n=1)
    assert outcomes.simulate(b, 0, 'long', 98.0, 104.0, 10) is None
