"""B1 Breakout Hunter (A10): after a quiet spell, a 15m close beyond the 20-bar range on double volume,
entered on the retest (fair-value gap top, or the breakout candle's midpoint).

Sources: Zerodha's price-volume breakout with retest entry, the TradingView session/candle-close breakout and
fair-value-gap scripts, and the volatility-compression ('squeeze') idea.
"""
import numpy as np
import pandas as pd

from quantpilot.strategies.base import rows

CODE, NAME = 'B1', 'Breakout Hunter'
LOOKBACK, VOLUME_X, SQUEEZE_PCT, SQUEEZE_BARS = 20, 2.0, .20, 16
MIN_STOP, TARGET_R, VALID_MINUTES, MAX_HOLD_HOURS = .003, 2.0, 60, 24
RULES = [
    'Waits for a quiet market: hourly volatility in its lowest 20% of the last 30 days.',
    'Then a 15-minute close above the last 20 candles\' high (or below their low) on at least double the usual volume.',
    'Does not chase: it places a resting order at the retest (the fair-value gap, or the middle of the breakout candle) for 1 hour.',
    'Stop beyond the breakout candle, target 2x the risk, out after 24 hours. Risks 1% of the account.',
]


def signals(f):
    prior_high = f.high.shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).max()
    prior_low = f.low.shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).min()
    volume = f.volume.shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).mean()
    quiet = f.atr_pct_1h.shift(1).rolling(SQUEEZE_BARS, min_periods=SQUEEZE_BARS).min() <= SQUEEZE_PCT
    loud = f.volume >= VOLUME_X*volume
    up_b, down_b = quiet & loud & (f.close > prior_high), quiet & loud & (f.close < prior_low)
    # decided one bar later (bar b+1), when the gap between b-1 and b+1 is known; bars must be contiguous
    step = pd.Series(f.index, index=f.index).diff() == pd.Timedelta(minutes=15)
    contiguous = step & step.shift(1, fill_value=False)
    long, short = up_b.shift(1, fill_value=False) & contiguous, down_b.shift(1, fill_value=False) & contiguous
    b_open, b_close, b_high, b_low = (f[k].shift(1) for k in ('open', 'close', 'high', 'low'))
    before_high, before_low = f.high.shift(2), f.low.shift(2)
    mid = (b_open+b_close)/2
    limit = np.where(long, np.where(f.low > before_high, f.low, mid), np.where(f.high < before_low, f.high, mid))
    stop = np.where(long, np.minimum(b_low, limit*(1-MIN_STOP)), np.maximum(b_high, limit*(1+MIN_STOP)))
    target = limit+np.where(long, 1, -1)*TARGET_R*np.abs(limit-stop)
    gap = np.where(long, f.low > before_high, f.high < before_low)
    side = np.where(long, 'long', np.where(short, 'short', None))
    reason = np.where(gap, 'Breakout after a quiet spell; resting at the fair-value gap',
                      'Breakout after a quiet spell; resting at the breakout candle\'s middle')
    out = rows(f, side, stop, target, MAX_HOLD_HOURS, reason, limit=limit, valid=VALID_MINUTES)
    return out[long | short]


def active(f, t):
    row = f.loc[t] if t in f.index else None
    if row is None or pd.isna(row.atr_pct_1h):
        return False, 'Off: warming up.'
    recent = f.atr_pct_1h.loc[:t].iloc[-SQUEEZE_BARS:]
    if recent.min() <= SQUEEZE_PCT:
        return True, 'On: the market has been quiet; it watches for a breakout on double volume.'
    return False, f'Off: the market is not quiet (hourly volatility at its {row.atr_pct_1h:.0%} level of 30 days); it waits for a squeeze.'
