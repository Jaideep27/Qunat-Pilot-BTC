"""Synthetic 5m candles for the A10 style tests."""
import numpy as np
import pandas as pd


def synthetic(days=45, seed=0, start='2026-01-05'):
    rng = np.random.default_rng(seed)
    n = days*288
    opens = pd.Timestamp(start, tz='UTC')+pd.to_timedelta(np.arange(n)*5, unit='min')
    close = 80000*np.exp(np.cumsum(rng.normal(0, .0015, n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(dict(event_time=opens, available_at=opens+pd.Timedelta(minutes=5), open=open_,
                             high=np.maximum(open_, close)*1.001, low=np.minimum(open_, close)*.999, close=close,
                             volume=rng.uniform(50, 150, n), taker_buy_volume=rng.uniform(20, 80, n)))
