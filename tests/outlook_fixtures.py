import numpy as np
import pandas as pd

from quantpilot import analogs


def panel(n=24*400, seed=1):
    rng = np.random.default_rng(seed)
    index = pd.date_range('2023-01-01', periods=n, freq='h', tz='UTC')
    close = 30000*np.exp(np.cumsum(rng.normal(0, .005, n)))
    frame = pd.DataFrame(index=index, data=dict(close=close, high=close*1.002, low=close*.998))
    for column in analogs.STATE:
        frame[column] = rng.normal(size=n)
    return frame


def ctx(report=None, **kw):
    from quantpilot import outlook
    p = panel(n=24*200)
    p['volume'] = 100.0
    for col in ('taker_buy_share_24h', 'volume_ratio_24h', 'top_position_ratio', 'premium', 'fear_greed',
                'global_ratio_change_24h', 'hours_to_next_cpi', 'hours_to_next_jobs', 'hours_to_next_fomc'):
        p[col] = 1.0
    now = p.index[-1]+pd.Timedelta(minutes=30)
    base = dict(panel=p, ranks=analogs.state_ranks(p), report=report or {}, macro_events=[],
                news_items=[], forecasts=[], price=float(p.close.iloc[-1]), price_time=now, now=now)
    base.update(kw)
    return outlook.Context(**base)
