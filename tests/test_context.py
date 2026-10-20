"""MarketContext: one causal, multi-timeframe snapshot per completed 5-minute bar."""
import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from agent_fixtures import START, frames, truncate
from quantpilot import context


@pytest.fixture(scope='module')
def data():
    return frames()


@pytest.fixture(scope='module')
def full(data):
    return context.context_frame(data)


def test_rows_are_identical_whether_built_from_all_data_or_truncated_at_their_cutoff(data, full):
    for cutoff in (START+pd.Timedelta(days=12, hours=3, minutes=25), START+pd.Timedelta(days=17, hours=9)):
        cut = context.context_frame(truncate(data, cutoff))
        a, b = full.loc[cutoff], cut.loc[cutoff]
        numeric = a.index[[isinstance(v, (int, float, np.floating)) for v in a.values]]
        np.testing.assert_allclose(a[numeric].astype(float), b[numeric].astype(float), rtol=1e-9, equal_nan=True)


def test_higher_timeframes_use_completed_bars_only(full):
    hour = START+pd.Timedelta(days=10, hours=10)
    inside = full.loc[hour+pd.Timedelta(minutes=5):hour+pd.Timedelta(minutes=55), 'rsi_1h']
    assert inside.nunique() == 1 and inside.iloc[0] == full.loc[hour, 'rsi_1h']
    assert full.loc[hour+pd.Timedelta(hours=1), 'rsi_1h'] != full.loc[hour, 'rsi_1h']


def test_swings_are_confirmed_two_bars_after_they_form():
    index = pd.date_range('2026-01-01 01:00', periods=9, freq='h', tz='UTC')
    high = pd.Series([1, 2, 3, 9, 3, 2, 1, 1, 1], index=index, dtype=float)
    confirmed = context.confirmed_swings(high, kind='high')
    assert list(confirmed.index) == [index[5]] and confirmed.iloc[0] == 9.0   # formed at bar 3, known at bar 5


def test_nearest_levels_bracket_the_price():
    times = pd.to_datetime(['2026-01-01 01:00', '2026-01-01 02:00', '2026-01-01 03:00'], utc=True)
    highs = pd.Series([105.0, 110.0, 102.0], index=times)
    lows = pd.Series([95.0, 90.0, 99.0], index=times)
    at = pd.to_datetime(['2026-01-01 02:30', '2026-01-01 03:30'], utc=True)
    levels = context.nearest_levels(highs, lows, at, price=pd.Series([100.0, 103.0], index=at))
    assert levels.loc[at[0]].tolist() == [105.0, 95.0]          # 102 not yet confirmed at 02:30
    assert levels.loc[at[1]].tolist() == [105.0, 99.0]          # 102 is below 103, so the next resistance is 105


def test_structure_brackets_the_price_whenever_a_level_exists(full):
    rows = full.iloc[288*8:]                                    # after a week of warm-up
    for tf in ('15m', '1h', '4h'):
        resistance, support = rows[f'resistance_{tf}'], rows[f'support_{tf}']
        assert (resistance.dropna() > rows.close[resistance.notna()]).all()
        assert (support.dropna() < rows.close[support.notna()]).all()
        assert resistance.notna().mean() > .7 and support.notna().mean() > .7   # NaN only at fresh extremes
    assert (rows.prior_day_high >= rows.prior_day_low).all() and rows.prior_week_high.notna().all()


def test_macro_blackout_runs_from_30_minutes_before_to_15_after():
    events = [dict(kind='cpi', label='CPI', scheduled_at='2026-01-14T13:30:00+00:00')]
    at = lambda s: context.macro_state(events, pd.Timestamp(s, tz='UTC'))['blackout']
    assert not at('2026-01-14 12:59') and at('2026-01-14 13:00') and at('2026-01-14 13:45')
    assert not at('2026-01-14 13:46')
    assert context.macro_state(events, pd.Timestamp('2026-01-14 10:00', tz='UTC'))['next']['kind'] == 'cpi'


def test_missing_or_stale_critical_inputs_block_entries(data):
    stale = dict(data, mark=data['mark'][data['mark'].available_at < START+pd.Timedelta(days=15)])
    row = context.context_frame(stale).iloc[-1]
    snap = context.snapshot_from_row(row)
    assert 'mark' in snap.blocking
    healthy = context.snapshot_from_row(context.context_frame(data).iloc[-1])
    assert healthy.blocking == []


def test_snapshot_is_immutable_and_carries_forecasts_known_by_the_cutoff(full):
    cutoff = full.index[-1]
    forecasts = [dict(cutoff=(cutoff-pd.Timedelta(minutes=10)).isoformat(), horizon='24h', probability_up=.52,
                      base_rate=.52, status='no_validated_skill', return_q10=-.03, return_q90=.03,
                      range_status='unconditional', model_version='m1'),
                 dict(cutoff=(cutoff+pd.Timedelta(minutes=10)).isoformat(), horizon='4h', probability_up=.9,
                      base_rate=.5, status='forecast', return_q10=-.01, return_q90=.01, range_status='model',
                      model_version='m1')]
    snap = context.snapshot(full, cutoff, forecasts=forecasts, macro_events=[], news_items=[])
    assert set(snap.forecasts) == {'24h'}                       # the 4h forecast is from the future
    assert snap.price == pytest.approx(full.loc[cutoff, 'close'])
    with pytest.raises(ValidationError):
        snap.price = 1.0


def test_24h_volatility_comes_from_the_last_week_of_hourly_returns(full):
    row = full.iloc[-1]
    hourly = full.close[full.index.minute == 0]
    expected = np.log(hourly[hourly.index <= row.name]).diff().iloc[-168:].std()*np.sqrt(24)
    assert row.volatility_24h == pytest.approx(expected, rel=1e-6)
    assert context.snapshot_from_row(row).volatility_24h == pytest.approx(expected, rel=1e-6)
