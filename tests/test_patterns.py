"""Pattern features for the rule engine: causal, computed from completed bars only."""
import numpy as np
import pandas as pd
import pytest

from agent_fixtures import START, frames, truncate
from quantpilot import patterns


@pytest.fixture(scope='module')
def data():
    d = frames(days=40)
    # A sharp, high-volume dump inside day 30: three 5m bars falling about 1.2% each.
    c = d['candles']
    at = 288*30+100
    c.loc[at:at+2, 'close'] = c.close.iloc[at-1]*np.exp(-.012*np.arange(1, 4))
    c.loc[at:, 'close'] = np.r_[c.close.iloc[at:at+3], c.close.iloc[at+3:]*c.close.iloc[at+2]/c.close.iloc[at+3]]
    c['open'] = np.r_[c.close.iloc[0], c.close.iloc[:-1]]
    c['high'] = np.maximum(c.open, c.close)*1.0005
    c['low'] = np.minimum(c.open, c.close)*.9995
    c.loc[at:at+2, 'volume'] = 900.0
    d['candles'] = c
    return d, c.available_at.iloc[at+2]


@pytest.fixture(scope='module')
def full(data):
    return patterns.pattern_frame(data[0])


CAUSAL = ['move_z_15m', 'move_z_1h', 'volume_z_15m', 'taker_share_15m', 'oi_change_15m', 'funding_z', 'vwap', 'vwap_z',
          'ema_stack_15m', 'ema_stack_1h', 'squeeze_on', 'atr_pct_1h', 'vol_forecast_1h', 'band4_high', 'band4_low',
          'range_pos_4h', 'range_pos_1h', 'support', 'resistance', 'sweep_low', 'break_down', 'retest_from_below',
          'body_15m', 'lower_wick_15m', 'rsi_div_bull']


def test_rows_do_not_change_when_later_data_is_appended(data, full):
    d, _ = data
    for cutoff in (START+pd.Timedelta(days=25, hours=3, minutes=25), START+pd.Timedelta(days=33, hours=9, minutes=15)):
        cut = patterns.pattern_frame(truncate(d, cutoff))
        np.testing.assert_allclose(full.loc[cutoff, CAUSAL].astype(float), cut.loc[cutoff, CAUSAL].astype(float),
                                   rtol=1e-9, equal_nan=True)


def test_a_sharp_dump_is_an_extreme_15_minute_move_with_high_volume(data, full):
    _, at = data
    row = full.loc[at]
    assert row.move_z_15m <= -4 and row.volume_z_15m >= 1.5 and row.ret_15m < -.03
    assert full.move_z_15m.loc[:at-pd.Timedelta(hours=2)].abs().quantile(.5) < 1.5


def test_vwap_restarts_at_utc_midnight(data, full):
    c = data[0]['candles'].set_index('available_at')
    first = START+pd.Timedelta(days=12, minutes=5)          # the first bar of a UTC day closes at 00:05
    typical = (c.high+c.low+c.close).loc[first]/3
    assert full.loc[first, 'vwap'] == pytest.approx(typical)
    assert np.isnan(full.loc[first, 'vwap_z'])               # one bar has no spread yet


def test_four_hour_band_is_anchored_at_the_block_open(data, full):
    c = data[0]['candles'].set_index('event_time')
    block = START+pd.Timedelta(days=20, hours=8)
    rows = full.loc[block+pd.Timedelta(minutes=5):block+pd.Timedelta(hours=4)]
    centre = np.sqrt(rows.band4_high*rows.band4_low)
    np.testing.assert_allclose(centre, c.open.loc[block], rtol=1e-12)
    assert rows.band4_high.nunique() == 1                   # fixed for the whole block
    inside = full.range_pos_4h.dropna()
    assert .7 < ((inside >= 0) & (inside <= 1)).mean() < 1


def test_funding_z_uses_previous_settlements_only():
    times = pd.date_range('2026-01-01', periods=100, freq='8h', tz='UTC')
    rates = np.r_[np.full(99, 1e-4)+np.tile([1e-5, -1e-5], 50)[:99], 5e-4]
    z = patterns.funding_z(pd.DataFrame(dict(event_time=times, available_at=times, funding_rate=rates)))
    assert z.iloc[-1] > 20 and abs(z.iloc[-2]) < 2


def test_level_events():
    atr = np.full(6, 10.0)
    support, resistance = np.full(6, 1000.0), np.full(6, 1100.0)
    #            sweep          break          retest          hold           -              -
    o = np.array([1005, 1004, 990, 994, 1006, 1050.0])
    h = np.array([1008, 1006, 991, 999.5, 1010, 1060.0])
    l = np.array([997, 980, 960, 985, 999.5, 1040.0])
    c = np.array([1004, 985, 965, 990, 1008, 1055.0])
    # bar 0: low 997 (0.3 ATR below support) but closes above: a sweep
    # bar 1: closes 985 (1.5 ATR below) with a 0.65 body: a clean break
    ev = patterns.level_events(o, h, l, c, support, resistance, atr)
    assert ev.sweep_low.tolist() == [1, 0, 0, 0, 0, 0]
    assert ev.break_down.tolist() == [0, 1, 1, 0, 0, 0]
    assert ev.retest_from_below.tolist() == [0, 0, 0, 1, 0, 0]     # rises back to the broken level, closes below
    assert ev.broken_support[3] == 1000.0
    assert ev.hold_support.tolist() == [0, 0, 0, 0, 1, 0]          # touches within 0.15 ATR, closes above


def test_squeeze_release_follows_a_compression():
    rng = np.random.default_rng(1)
    close = 100+np.r_[rng.normal(0, .02, 80), np.cumsum(np.full(40, .8))]
    bars = pd.DataFrame(dict(open=close, high=close+.6, low=close-.6, close=close))
    s = patterns.squeeze(bars)
    assert s.squeeze_on.iloc[40:80].mean() > .8
    first = s.squeeze_release.to_numpy().nonzero()[0]
    assert len(first) and 80 <= first[0] <= 90


def test_forward_only_inputs_are_missing_before_collection_and_summed_after(data):
    d, _ = data
    base = patterns.pattern_frame(d)
    assert base.liq_long_usd_15m.isna().all() and base.spread_bps.isna().all()
    times = pd.date_range(START+pd.Timedelta(days=39), periods=60, freq='min', tz='UTC')
    liq = pd.DataFrame(dict(event_time=times, available_at=times+pd.Timedelta(minutes=1),
                            long_liq_usd=1000.0, short_liq_usd=0.0, long_liq_qty=.01, short_liq_qty=0.0, liq_count=1.0))
    out = patterns.pattern_frame(d | dict(liquidations=liq))
    at = times[30]+pd.Timedelta(minutes=1)
    at = at.ceil('5min')
    assert out.loc[at, 'liq_long_usd_15m'] == pytest.approx(15000.0)
    assert out.loc[START+pd.Timedelta(days=30), 'liq_long_usd_15m'] != out.loc[START+pd.Timedelta(days=30), 'liq_long_usd_15m']


def test_regime_order():
    row = dict(atr_pct_1h=.98, ret_4h=-.05, adx_1h=30, ema_stack_1h=1)
    assert patterns.regime(row) == 'PANIC'
    assert patterns.regime(row | dict(ret_4h=.01)) == 'HIGH_VOL'
    assert patterns.regime(row | dict(atr_pct_1h=.5)) == 'TREND_UP'
    assert patterns.regime(row | dict(atr_pct_1h=.5, ema_stack_1h=-1)) == 'TREND_DOWN'
    assert patterns.regime(row | dict(atr_pct_1h=.5, adx_1h=18)) == 'RANGE'
    assert patterns.regime(dict(atr_pct_1h=None, ret_4h=None, adx_1h=None, ema_stack_1h=None)) == 'RANGE'


def test_order_book_snapshot_is_joined_only_while_fresh(data):
    d, _ = data
    times = pd.date_range(START+pd.Timedelta(days=38), periods=24*60, freq='min', tz='UTC')
    size = np.linspace(10, 20, len(times))
    depth = pd.DataFrame(dict(event_time=times, available_at=times+pd.Timedelta(seconds=5), spread_bps=.2,
                              imbalance_15bps=.1, bid_qty_15bps=size, ask_qty_15bps=size))
    out = patterns.pattern_frame(d | dict(depth=depth))
    inside = times[-1].floor('5min')
    assert out.loc[inside, 'spread_bps'] == pytest.approx(.2)
    assert out.loc[inside, 'depth_15bps_pct'] == pytest.approx(1.0)       # the deepest book of the day so far
    assert np.isnan(out.loc[inside+pd.Timedelta(minutes=10), 'spread_bps'])  # older than 3 minutes: missing
