"""No look-ahead for the A10 styles: scrambling every bar after a cut-off must not change any frame value or signal
decided before it. A planted leak (a value read three bars ahead) must be caught by the same check."""
import numpy as np
import pandas as pd
import pytest

from quantpilot.strategies import breakout, market, range_trader, trend
from style_fixtures import synthetic


def scramble(candles, cut, seed=99):
    rng = np.random.default_rng(seed)
    c = candles.copy()
    later = c.available_at > cut
    factor = np.exp(np.cumsum(rng.normal(0, .01, int(later.sum()))))*1.1
    for column in ('open', 'high', 'low', 'close'):
        c.loc[later, column] = c.loc[later, column].to_numpy()*factor
    c.loc[later, 'volume'] = c.loc[later, 'volume'].to_numpy()*rng.uniform(.2, 5, int(later.sum()))
    return c


@pytest.fixture(scope='module')
def worlds():
    candles = synthetic(days=60, seed=21)
    cut = candles.available_at.iloc[int(len(candles)*.8)]
    return candles, scramble(candles, cut), cut


def same_before(a, b, cut):
    pd.testing.assert_frame_equal(a.loc[:cut], b.loc[:cut], check_dtype=False)


def test_the_style_frame_and_intraday_signals_ignore_the_future(worlds):
    real, other, cut = worlds
    fa, fb = market.style_frame(real), market.style_frame(other)
    same_before(fa, fb, cut)
    for signals in (range_trader.signals, breakout.signals):
        same_before(signals(fa), signals(fb), cut)


def test_the_trend_vote_ignores_the_future(worlds):
    real, other, cut = worlds
    da, db = market.daily_bars(real), market.daily_bars(other)
    same_before(trend.frame(da), trend.frame(db), cut)


def test_a_planted_leak_is_caught(worlds):
    real, other, cut = worlds
    leaky = lambda f: f.assign(peek=f.close.shift(-3))
    with pytest.raises(AssertionError):
        same_before(leaky(market.style_frame(real)), leaky(market.style_frame(other)), cut)
