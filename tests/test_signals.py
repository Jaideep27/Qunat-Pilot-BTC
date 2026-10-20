"""Pre-registered signal families: each outputs +1/-1/0 from information known at the bar's close."""
import numpy as np
import pandas as pd
import pytest

from quantpilot import signals


def daily(close, **cols):
    index = pd.date_range('2020-01-02', periods=len(close), freq='D', tz='UTC')
    close = np.asarray(close, float)
    return pd.DataFrame(dict(close=close, high=close, low=close, **cols), index=index)


def test_donchian_breaks_out_above_the_previous_high_only():
    frame = daily([10, 11, 12, 11, 13, 9])
    s = signals.donchian(frame, L=2)
    assert s.tolist() == [0, 0, 1, 0, 1, -1]


def test_momentum_agreement_needs_every_lookback_to_agree():
    frame = daily([10, 11, 12, 11, 10, 9])
    s = signals.momentum_agree(frame, lookbacks=(1, 2))
    assert s.tolist() == [0, 0, 1, 0, -1, -1]


def test_signals_never_use_future_rows():
    rng = np.random.default_rng(0)
    n = 900
    close = 100*np.exp(np.cumsum(rng.normal(0, .02, n)))
    frame = daily(close, funding_rate=rng.normal(1e-4, 1e-4, n), open_interest=1e9*np.exp(np.cumsum(rng.normal(0, .01, n))),
                  coinbase_premium=rng.normal(0, 1e-3, n))
    for family in signals.FAMILIES.values():
        if family['track'] != 'swing':
            continue
        for params in family['grid']:
            full = family['fn'](frame, **params)
            cut = family['fn'](frame.iloc[:700], **params)
            assert (full.iloc[:700] == cut).all(), (family['id'], params)


def test_funding_crowding_fades_crowded_longs_after_a_rise():
    n = 400
    funding = np.full(n, 1e-4)
    funding[-3:] = 1e-3                         # extreme recent funding
    close = np.linspace(100, 120, n)            # price rising over the last week
    s = signals.funding_crowding(daily(close, funding_rate=funding, open_interest=np.ones(n)), p=.05, oi_filter=False)
    assert s.iloc[-1] == -1 and s.iloc[:-3].eq(0).all()


def hourly(n=72, start='2024-01-01 01:00'):
    index = pd.date_range(start, periods=n, freq='h', tz='UTC')
    return pd.DataFrame(dict(close=100.0, high=100.0, low=100.0), index=index)


def test_session_momentum_calls_only_at_its_hour_and_fixes_the_horizon():
    frame = hourly()
    frame.loc['2024-01-02 00:00':'2024-01-02 08:00', 'close'] = np.linspace(100, 102, 9)
    s = signals.session_momentum(frame, h0=8, m=.01)
    assert s.loc['2024-01-02 08:00'] == 1 and s.abs().sum() == 1
    assert signals.FAMILIES['I3']['horizon'](dict(h0=8, m=.01)) == 16


def test_flush_rebound_needs_both_oi_and_price_drops():
    frame = hourly(10)
    frame['open_interest'] = 100.0
    frame.iloc[-1, frame.columns.get_loc('open_interest')] = 96.0
    frame.iloc[-1, frame.columns.get_loc('close')] = 97.0
    assert signals.flush_rebound(frame, x=.03, y=.02).iloc[-1] == 1
    assert signals.flush_rebound(frame, x=.05, y=.02).iloc[-1] == 0


def test_grids_follow_the_preregistration():
    sizes = {k: len(v['grid']) for k, v in signals.FAMILIES.items()}
    assert sizes == dict(S1=3, S2=3, S3=6, S4=3, S5=6, I1=9, I2=6, I3=6, I4=6, I5=6)
    assert signals.FAMILIES['S1']['horizons'] == (3, 7, 14) and signals.FAMILIES['I1']['horizons'] == (4, 12, 24)
