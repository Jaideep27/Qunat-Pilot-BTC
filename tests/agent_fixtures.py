"""Synthetic market frames in the `event_study.load_market` format, for context/plan/simulator tests."""
import numpy as np
import pandas as pd

START = pd.Timestamp('2026-01-05 00:00', tz='UTC')   # a Monday


def frames(days=20, seed=7, start=START, drift=0.0):
    rng = np.random.default_rng(seed)
    n = days*288
    opens = start+pd.to_timedelta(np.arange(n)*5, unit='min')
    close = 80000*np.exp(np.cumsum(rng.normal(drift, .0015, n)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, .0008, n))*close
    candles = pd.DataFrame(dict(event_time=opens, available_at=opens+pd.Timedelta(minutes=5), open=open_,
                                high=np.maximum(open_, close)+spread, low=np.minimum(open_, close)-spread, close=close,
                                volume=rng.uniform(50, 150, n), taker_buy_volume=rng.uniform(20, 80, n)))
    closes = opens+pd.Timedelta(minutes=5)
    mark = pd.DataFrame(dict(event_time=opens, available_at=closes, mark_price=close*(1+rng.normal(0, 1e-4, n))))
    index = pd.DataFrame(dict(event_time=opens, available_at=closes, index_price=close))
    positioning = pd.DataFrame(dict(event_time=closes, available_at=closes+pd.Timedelta(minutes=5),
                                    open_interest=90000*np.exp(np.cumsum(rng.normal(0, .001, n))),
                                    global_ratio=1.5+rng.normal(0, .05, n), top_account_ratio=1.2+rng.normal(0, .05, n),
                                    top_position_ratio=1.1+rng.normal(0, .05, n)))
    settlements = pd.date_range(start, opens[-1], freq='8h')
    funding = pd.DataFrame(dict(event_time=settlements, available_at=settlements+pd.Timedelta(seconds=1),
                                funding_rate=rng.normal(1e-4, 5e-5, len(settlements))))
    return dict(candles=candles, mark=mark, index=index, positioning=positioning, funding=funding)


def truncate(data, cutoff):
    return {k: v[v.available_at <= cutoff].reset_index(drop=True) for k, v in data.items()}
