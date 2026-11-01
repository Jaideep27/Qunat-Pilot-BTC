"""A10 style rules on synthetic markets: each fires only in its own conditions."""
import numpy as np
import pandas as pd
import pytest

from quantpilot.strategies import base, breakout, range_trader, trend


def daily(closes, start='2025-01-01'):
    closes = np.asarray(closes, float)
    idx = pd.date_range(start, periods=len(closes), freq='D', tz='UTC')
    opens = np.r_[closes[0], closes[:-1]]
    return pd.DataFrame(dict(open=opens, high=np.maximum(opens, closes)*1.01, low=np.minimum(opens, closes)*.99,
                             close=closes, volume=1.0), index=idx)


def test_trend_votes_long_in_a_steady_rise_short_in_a_fall_and_flat_in_chop():
    rng = np.random.default_rng(1)
    up = daily(100*np.exp(np.cumsum(np.full(200, .004))))
    down = daily(100*np.exp(np.cumsum(np.full(200, -.004))))
    chop = daily(100*np.exp(rng.normal(0, .02, 200)))     # no trend at any horizon
    assert trend.frame(up).vote.iloc[-1] == 1
    assert trend.frame(down).vote.iloc[-1] == -1
    assert (trend.frame(chop).vote.iloc[130:] == 0).mean() > .5
    assert trend.frame(up).vote.iloc[:120].eq(0).all()     # needs 120 days of history first


def test_trend_intent_has_a_3_atr_stop_no_target_and_volatility_scaled_risk():
    f = trend.frame(daily(100*np.exp(np.cumsum(np.full(200, .004)))))
    t = f.index[-1]
    it = trend.intent(f, t)
    row = f.loc[t]
    assert it.side == 'long' and it.target is None and it.stop == pytest.approx(row.close-3*row.atr20)
    assert it.risk_scale == pytest.approx(min(1.0, .40/row.sigma30))
    assert it.max_hold_hours >= 24*365 and it.style == 'T1'


def test_trend_holds_only_while_the_vote_keeps_its_side():
    f = trend.frame(daily(100*np.exp(np.cumsum(np.full(200, .004)))))
    assert trend.holds(f, f.index[-1], 'long') and not trend.holds(f, f.index[-1], 'short')


def band_frame(n=40, regime='RANGE', low=None, close=None, high=None):
    idx = pd.date_range('2026-03-02 00:15', periods=n, freq='15min', tz='UTC')
    f = pd.DataFrame(dict(open=100.0, high=100.5, low=99.5, close=100.0, volume=10.0, band4_high=102.0, band4_low=98.0,
                          atr_pct_1h=.5, regime=regime), index=idx)
    f['band4_mid'] = 100.0
    if low is not None:
        f.loc[idx[-1], ['low', 'close', 'high']] = low, close, high
    return f


def test_range_trader_buys_a_poke_below_the_band_that_closes_back_inside_only_in_range():
    f = band_frame(low=97.5, close=98.3, high=98.6)
    sig = range_trader.signals(f)
    assert list(sig.index) == [f.index[-1]]
    s = sig.iloc[0]
    assert s.side == 'long' and s.target == 100.0 and s.stop == pytest.approx(98.0-.5*4.0)
    assert s.max_hold_hours == 4
    assert range_trader.signals(band_frame(regime='TREND_UP', low=97.5, close=98.3, high=98.6)).empty
    assert range_trader.signals(band_frame(low=97.5, close=97.8, high=98.6)).empty     # closed outside: no
    short = range_trader.signals(band_frame(low=101.5, close=101.7, high=102.4)).iloc[0]
    assert short.side == 'short' and short.stop == pytest.approx(104.0)


def breakout_frame(squeeze=True, volume=3.0, fvg=True):
    n = 60
    idx = pd.date_range('2026-03-02 00:15', periods=n, freq='15min', tz='UTC')
    f = pd.DataFrame(dict(open=100.0, high=100.2, low=99.8, close=100.0, volume=1.0, atr_pct_1h=.1 if squeeze else .6),
                     index=idx)
    b = idx[-2]
    f.loc[b, ['open', 'high', 'low', 'close', 'volume']] = 100.0, 101.2, 99.9, 101.0, volume
    f.loc[idx[-1], ['open', 'high', 'low', 'close']] = (101.0, 101.6, 100.7 if fvg else 100.1, 101.4)
    return f


def test_breakout_needs_a_squeeze_and_double_volume_and_rests_a_limit_at_the_gap():
    sig = breakout.signals(breakout_frame())
    assert list(sig.index) == [breakout_frame().index[-1]]
    s = sig.iloc[0]
    assert s.side == 'long' and s.limit == pytest.approx(100.7)            # top of the fair-value gap = low(b+1)
    assert s.stop == pytest.approx(min(99.9, 100.7*(1-.003)))
    assert s.target == pytest.approx(s.limit+2*(s.limit-s.stop)) and s.valid_minutes == 60 and s.max_hold_hours == 24
    assert breakout.signals(breakout_frame(squeeze=False)).empty
    assert breakout.signals(breakout_frame(volume=1.5)).empty
    mid = breakout.signals(breakout_frame(fvg=False)).iloc[0]
    assert mid.limit == pytest.approx((100.0+101.0)/2)                       # no gap: midpoint of the breakout bar


def test_sizing_risks_one_percent_at_the_stop_and_caps_leverage():
    it = base.Intent(style='R1', side='long', time=pd.Timestamp('2026-03-02', tz='UTC'), ref=100.0, stop=99.0,
                     target=102.0, max_hold_hours=4, reason='x')
    assert base.quantity(it, 10_000.0) == pytest.approx(100.0/1.0)
    assert base.quantity(it, 10_000.0, leverage=2.0) == pytest.approx(100.0)
    tight = base.Intent(**{**it.__dict__, 'stop': 99.99})
    assert base.quantity(tight, 10_000.0) == pytest.approx(2*10_000/100.0)   # 2x cap binds
    plan = base.plan(it, 10_000.0, hit_id='R1:x')
    assert plan['min_reward_risk'] is None and plan['style'] == 'R1' and plan['playbook'] == 'R1' and plan['leverage'] == 2.0
