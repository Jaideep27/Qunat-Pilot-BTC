"""Pre-registered signal families (docs/research/preregistration.md, section 4).

Each function returns +1 (long), -1 (short) or 0 (no call) per bar, using only rows up to and
including that bar. Grids list the only parameter values allowed; holding periods (H) are an extra
grid dimension except where the rule fixes them.
"""
from __future__ import annotations

from itertools import product

import numpy as np
import pandas as pd


def _calls(long, short):
    out = pd.Series(0, index=long.index, dtype=int)
    out[long.fillna(False).astype(bool)] = 1
    out[short.fillna(False).astype(bool)] = -1
    return out


def trailing_percentile(series, window, min_periods=None):
    """Percentile rank of each value within its trailing window (inclusive of itself)."""
    return series.rolling(window, min_periods=min_periods or window//2).rank(pct=True)


def _zscore(series, window):
    return (series-series.rolling(window, min_periods=window//2).mean())/series.rolling(window, min_periods=window//2).std()


def _persistent(z, z0, k):
    return _calls(z.rolling(k).min() > z0, z.rolling(k).max() < -z0)


# Swing track (daily bars)

def donchian(frame, L):
    high, low = frame.high.shift(1).rolling(L).max(), frame.low.shift(1).rolling(L).min()
    return _calls(frame.close > high, frame.close < low)


def momentum_agree(frame, lookbacks):
    returns = [frame.close/frame.close.shift(k)-1 for k in lookbacks]
    up = pd.concat([r > 0 for r in returns], axis=1).all(axis=1) & pd.concat([r.notna() for r in returns], axis=1).all(axis=1)
    down = pd.concat([r < 0 for r in returns], axis=1).all(axis=1)
    return _calls(up, down)


def funding_crowding(frame, p, oi_filter):
    level = trailing_percentile(frame.funding_rate.rolling(3).mean(), 365, 180)
    week = frame.close/frame.close.shift(7)-1
    ok = (frame.open_interest/frame.open_interest.shift(7)-1 > 0) if oi_filter else True
    return _calls((level <= p) & (week < 0) & ok, (level >= 1-p) & (week > 0) & ok)


def vol_compression(frame, q):
    vol = np.log(frame.close).diff().rolling(20).std()
    quiet = trailing_percentile(vol, 365, 180) <= q
    trend = frame.close.rolling(50).mean()
    return _calls(quiet & (frame.close > trend), quiet & (frame.close <= trend) & trend.notna())


def premium_persistence(frame, z0, k, window=30):
    return _persistent(_zscore(frame.coinbase_premium, window), z0, k)


# Intraday track (hourly bars)

def flush_rebound(frame, x, y):
    oi = frame.open_interest/frame.open_interest.shift(4)-1
    move = frame.close/frame.close.shift(4)-1
    return _calls((oi <= -x) & (move <= -y), pd.Series(False, index=frame.index))


def crowding_fade(frame, q, s):
    crowd = trailing_percentile(frame.global_ratio, 720, 360)
    paid = trailing_percentile(frame.funding_rate, 720, 360)
    calm = (frame.close/frame.close.shift(24)-1).abs() < s
    return _calls((crowd <= 1-q) & (paid <= 1-q) & calm, (crowd >= q) & (paid >= q) & calm)


def session_momentum(frame, h0, m):
    index = pd.DatetimeIndex(frame.index)
    midnight = frame.close.where(index.hour == 0).reindex(index)
    opening = pd.Series(midnight.to_numpy(), index=index.floor('D')).groupby(level=0).first()
    since = frame.close/opening.reindex(index.floor('D')).to_numpy()-1
    at = pd.Series(index.hour == h0, index=frame.index)
    return _calls(at & (since > m), at & (since < -m))


def taker_persistence(frame, z0, k):
    return _persistent(_zscore(frame.taker_share_1h, 168), z0, k)


def hourly_premium(frame, z0, k):
    return premium_persistence(frame, z0, k, window=168)


def _grid(**values):
    keys = list(values)
    return [dict(zip(keys, combo)) for combo in product(*values.values())]


SWING_H, INTRADAY_H = (3, 7, 14), (4, 12, 24)
FAMILIES = {
    'S1': dict(track='swing', fn=donchian, grid=_grid(L=[20, 55, 100]), horizons=SWING_H),
    'S2': dict(track='swing', fn=momentum_agree, grid=_grid(lookbacks=[(7, 30), (7, 30, 90), (14, 60, 120)]),
               horizons=SWING_H),
    'S3': dict(track='swing', fn=funding_crowding, grid=_grid(p=[.05, .10, .20], oi_filter=[False, True]),
               horizons=SWING_H),
    'S4': dict(track='swing', fn=vol_compression, grid=_grid(q=[.10, .20, .30]), horizons=SWING_H),
    'S5': dict(track='swing', fn=premium_persistence, grid=_grid(z0=[1.0, 1.5, 2.0], k=[1, 3]), horizons=SWING_H),
    'I1': dict(track='intraday', fn=flush_rebound, grid=_grid(x=[.02, .03, .05], y=[.02, .03, .04]),
               horizons=INTRADAY_H),
    'I2': dict(track='intraday', fn=crowding_fade, grid=_grid(q=[.80, .90, .95], s=[.01, .02]), horizons=INTRADAY_H),
    'I3': dict(track='intraday', fn=session_momentum, grid=_grid(h0=[8, 14, 16], m=[.005, .01]), horizons=None,
               horizon=lambda params: 24-params['h0']),
    'I4': dict(track='intraday', fn=taker_persistence, grid=_grid(z0=[1.5, 2.0, 2.5], k=[2, 3]), horizons=INTRADAY_H),
    'I5': dict(track='intraday', fn=hourly_premium, grid=_grid(z0=[1.5, 2.0, 2.5], k=[1, 3]), horizons=INTRADAY_H),
}
for _id, _family in FAMILIES.items():
    _family['id'] = _id

# High-recall primaries for meta-labeling (section 6): a side every bar, H fixed.
PRIMARIES = {
    'M1': dict(track='swing', side=lambda f: np.sign(f.close/f.close.rolling(50).mean()-1).fillna(0).astype(int),
               horizon=7),
    'M2': dict(track='intraday', side=lambda f: np.sign(f.close/f.close.shift(24)-1).fillna(0).astype(int),
               horizon=12),
}


def configurations(family):
    """Every (params, H) pair of a family, in a stable order."""
    for params in family['grid']:
        for horizon in (family['horizons'] or (family['horizon'](params),)):
            yield params, horizon
