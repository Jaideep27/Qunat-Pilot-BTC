"""T1 Trend Rider (A10): daily time-series momentum with a moving-average filter and volatility-scaled risk.

Sources: Dr Tan's volatility-adjusted momentum (fixed: size scales with volatility instead of an on/off filter),
the Zerodha moving-average trend filter, and the time-series momentum literature (Moskowitz et al.; Hurst et al.).
"""
import numpy as np
import pandas as pd

from quantpilot.features.core import wilder
from quantpilot.strategies.base import Intent

CODE, NAME = 'T1', 'Trend Rider'
LOOKBACKS, SMA, ATR_DAYS, STOP_ATR, VOL_TARGET, VOL_DAYS = (20, 60, 120), 50, 20, 3.0, .40, 30
MAX_HOLD_HOURS = 24*3650     # no time stop: it leaves when the trend vote breaks or the stop is hit
RULES = [
    'Looks once a day at the daily close (00:00 UTC).',
    'Goes long when BTC is up over 20, 60 and 120 days and above its 50-day average; short when all are down and it is below.',
    'Otherwise stays flat. It leaves when that agreement breaks.',
    'Stop 3 x the 20-day average daily range from entry. Risks 1% of the account, less when BTC is unusually volatile.',
]


def frame(daily):
    """Daily bars plus the vote (+1, -1 or 0) and its inputs, all known at each day's close."""
    f = daily.copy()
    c = f.close
    for n in LOOKBACKS:
        f[f'r{n}'] = c/c.shift(n)-1
    f['sma50'] = c.rolling(SMA, min_periods=SMA).mean()
    tr = pd.concat([f.high-f.low, (f.high-c.shift()).abs(), (f.low-c.shift()).abs()], axis=1).max(axis=1)
    f['atr20'] = wilder(tr, ATR_DAYS)
    f['sigma30'] = np.log(c).diff().rolling(VOL_DAYS, min_periods=VOL_DAYS).std()*np.sqrt(365)
    rets = f[[f'r{n}' for n in LOOKBACKS]]
    ready = rets.notna().all(axis=1) & f.sma50.notna() & f.atr20.notna() & f.sigma30.notna()
    up = (rets > 0).all(axis=1) & (c > f.sma50)
    down = (rets < 0).all(axis=1) & (c < f.sma50)
    f['vote'] = np.where(ready & up, 1, np.where(ready & down, -1, 0))
    return f


def intent(f, t):
    row = f.loc[t]
    if row.vote == 0:
        return None
    side = 'long' if row.vote == 1 else 'short'
    stop = row.close-row.vote*STOP_ATR*row.atr20
    return Intent(style=CODE, side=side, time=pd.Timestamp(t), ref=float(row.close), stop=float(stop), target=None,
                  max_hold_hours=MAX_HOLD_HOURS, risk_scale=float(min(1.0, VOL_TARGET/row.sigma30)),
                  reason=f"20/60/120-day returns {row.r20:+.1%}/{row.r60:+.1%}/{row.r120:+.1%}, close vs 50-day average "
                         f"{row.close/row.sma50-1:+.1%}")


def holds(f, t, side):
    """True while the vote still agrees with an open position's side."""
    return int(f.loc[t].vote) == (1 if side == 'long' else -1)


def active(f, t):
    vote = int(f.loc[t].vote) if t in f.index else 0
    if vote == 0:
        return False, 'Off: the 20/60/120-day trends and the 50-day average do not agree, so it stays flat.'
    return True, f"On: every trend measure points {'up' if vote == 1 else 'down'}."
