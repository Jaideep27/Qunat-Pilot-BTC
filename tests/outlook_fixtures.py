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
