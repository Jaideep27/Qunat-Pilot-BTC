"""R1 Range Trader (A10): in sideways markets, fade a poke outside the 4h 80% band once price closes back inside.

Sources: the outlook's 4h range band (the project's one validated forecasting skill) and the price-action
'sweep and reclaim' entry from the owner's videos.
"""
import numpy as np
import pandas as pd

from quantpilot.strategies.base import rows

CODE, NAME = 'R1', 'Range Trader'
STOP_WIDTH, MAX_HOLD_HOURS = .5, 4
RULES = [
    'Works only when the market is sideways (the rule engine regime is RANGE).',
    'Buys when a 15-minute candle dips below the usual 4-hour range and closes back inside it; sells the mirror at the top.',
    'Target: the middle of the range. Stop: half a range-width beyond the edge. Out after 4 hours either way.',
    'Risks 1% of the account per trade.',
]


def signals(f):
    ready = f.atr_pct_1h.notna() & f.band4_high.notna() & f.band4_low.notna() & (f.regime == 'RANGE')
    width = f.band4_high-f.band4_low
    long = ready & (f.low < f.band4_low) & (f.close > f.band4_low) & (f.close < f.band4_mid)
    short = ready & (f.high > f.band4_high) & (f.close < f.band4_high) & (f.close > f.band4_mid)
    side = np.where(long, 'long', np.where(short, 'short', None))
    stop = np.where(long, f.band4_low-STOP_WIDTH*width, f.band4_high+STOP_WIDTH*width)
    reason = np.where(long, 'Dipped below the 4h range and closed back inside', 'Rose above the 4h range and closed back inside')
    out = rows(f, side, stop, f.band4_mid, MAX_HOLD_HOURS, reason)
    return out[long | short]


def active(f, t):
    row = f.loc[t] if t in f.index else None
    if row is None or pd.isna(row.atr_pct_1h):
        return False, 'Off: warming up.'
    if row.regime != 'RANGE':
        return False, f"Off: the market is {str(row.regime).replace('_', ' ').lower()}, and Range Trader only trades sideways markets."
    return True, 'On: the market is sideways; it waits for a poke outside the 4h range.'
