"""Time Machine (A10, recorded only): bootstrap odds of target-first / stop-first / neither."""
import numpy as np
import pandas as pd
import pytest

from quantpilot.strategies import timemachine


def closes(days=70, drift=0.0, vol=.002, seed=3):
    rng = np.random.default_rng(seed)
    n = days*288
    idx = pd.Timestamp('2026-01-01', tz='UTC')+pd.to_timedelta(np.arange(1, n+1)*5, unit='min')
    return pd.Series(100_000*np.exp(np.cumsum(rng.normal(drift, vol, n))), index=idx)


def test_same_seed_same_answer_and_the_shares_add_up():
    c = closes()
    t = c.index[-1]
    a = timemachine.odds(c, t, 'long', 100_000.0, 99_000.0, 101_000.0, hours=4, paths=500)
    b = timemachine.odds(c, t, 'long', 100_000.0, 99_000.0, 101_000.0, hours=4, paths=500)
    assert a == b
    assert a['target_first']+a['stop_first']+a['neither'] == pytest.approx(1.0)
    assert a['paths'] == 500 and a['days'] == 60


def test_a_rising_market_favours_the_long_target():
    up = closes(drift=.0004)
    t = up.index[-1]
    long = timemachine.odds(up, t, 'long', 1.0, .99, 1.01, hours=24, paths=800)
    short = timemachine.odds(up, t, 'short', 1.0, 1.01, .99, hours=24, paths=800)
    assert long['target_first'] > long['stop_first'] and short['stop_first'] > short['target_first']


def test_only_the_past_is_used():
    c = closes()
    t = c.index[len(c)//2]
    before = timemachine.odds(c, t, 'long', 1.0, .99, 1.01, hours=4, paths=300)
    later = c.copy()
    later.loc[later.index > t] *= 3.0          # change the future wildly
    assert timemachine.odds(later, t, 'long', 1.0, .99, 1.01, hours=4, paths=300) == before


def test_a_stop_only_trade_reports_stop_odds_and_the_median_result():
    c = closes()
    out = timemachine.odds(c, c.index[-1], 'long', 1.0, .95, None, hours=24*30, paths=300)
    assert out['target_first'] == 0 and 0 <= out['stop_first'] <= 1 and 'median_r' in out
