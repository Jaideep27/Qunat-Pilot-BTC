"""The style frame must read the market exactly as the rule engine's pattern frame does (A10)."""
import numpy as np
import pandas as pd
import pytest

from quantpilot.features import patterns
from quantpilot.strategies import market
from style_fixtures import synthetic


@pytest.fixture(scope='module')
def candles():
    return synthetic()


def test_style_frame_matches_the_pattern_frame_at_15m_closes(candles):
    sf = market.style_frame(candles)
    pf = patterns.pattern_frame(dict(candles=candles))
    closes = pf.index[pf.index == pf.index.floor('15min')]
    assert sf.index.equals(closes.intersection(sf.index)) and len(sf) > 0.9*len(closes)
    for column in ('atr_pct_1h', 'ret_4h', 'adx_1h', 'ema_stack_1h', 'band4_high', 'band4_low'):
        pd.testing.assert_series_equal(sf[column], pf.loc[sf.index, column], check_names=False)


def test_regime_is_the_rule_engine_regime(candles):
    sf = market.style_frame(candles)
    expected = sf.apply(lambda r: patterns.regime(r.to_dict()), axis=1)
    assert (sf.regime == expected).all()
    assert set(sf.regime.dropna()) <= {'RANGE', 'TREND_UP', 'TREND_DOWN', 'HIGH_VOL', 'PANIC'}


def test_15m_bars_are_completed_bars_indexed_by_their_close(candles):
    sf = market.style_frame(candles)
    t = sf.index[100]
    window = candles[(candles.available_at > t-pd.Timedelta(minutes=15)) & (candles.available_at <= t)]
    assert len(window) == 3
    row = sf.loc[t]
    assert row.open == window.open.iloc[0] and row.close == window.close.iloc[-1]
    assert row.high == window.high.max() and row.low == window.low.min()
    assert row.volume == pytest.approx(window.volume.sum())


def test_daily_bars_keep_only_complete_days_indexed_by_their_close(candles):
    daily = market.daily_bars(candles.iloc[:-10])   # the last day is incomplete
    assert daily.index[0] == pd.Timestamp('2026-01-06', tz='UTC')
    assert daily.index[-1] == pd.Timestamp('2026-02-18', tz='UTC')   # day 44 is complete, day 45 is not
    day = candles[(candles.event_time >= '2026-01-05') & (candles.event_time < '2026-01-06')]
    assert daily.iloc[0].close == day.close.iloc[-1] and daily.iloc[0].high == day.high.max()


def test_nothing_after_a_cutoff_changes_earlier_rows(candles):
    full = market.style_frame(candles)
    cut = full.index[len(full)//2]
    part = market.style_frame(candles[candles.available_at <= cut])
    pd.testing.assert_frame_equal(part, full.loc[:cut])


def test_support_and_resistance_match_the_context_frame(candles):
    from quantpilot.features import context
    sf = market.style_frame(candles)
    cf = context.context_frame(dict(candles=candles))
    for column in ('support_1h', 'resistance_1h', 'support_4h', 'resistance_4h', 'atr_1h'):
        pd.testing.assert_series_equal(sf[column], cf.loc[sf.index, column], check_names=False)
