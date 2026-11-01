"""What the styles see: completed 15m and daily bars plus the rule engine's regime and 4h band inputs.

Every column is computed exactly as `features.patterns.pattern_frame` defines it (tested), but only the few the
styles need, so the four-year history test fits in the laptop's memory. Live and history use this same code.
"""
import numpy as np
import pandas as pd

from quantpilot.features import context, patterns
from quantpilot.features.core import div

REGIME_INPUTS = ('atr_pct_1h', 'ret_4h', 'adx_1h', 'ema_stack_1h')


def candles_of(candles):
    return patterns._candles(dict(candles=candles))


def regimes(frame):
    """`patterns.regime` for every row, vectorised (same order of checks)."""
    pct, ret4, adx, stack = (frame[k] for k in REGIME_INPUTS)
    panic = (pct >= .97) & (ret4.abs() >= .04)
    high = pct >= .85
    trend = (adx >= 25) & stack.isin((1.0, -1.0))
    up = np.where(stack == 1, 'TREND_UP', 'TREND_DOWN')
    return pd.Series(np.select([panic, high, trend], ['PANIC', 'HIGH_VOL', up], 'RANGE'), index=frame.index)


def style_frame(candles):
    """One row per completed 15m bar, indexed by its close: the bar, the regime inputs, the 4h band and the regime."""
    candles = candles_of(candles)
    base = candles.set_index('available_at')
    index = base.index
    logc = np.log(base.close)
    one_hour = context.timeframe_bars(candles, '1h', 12)
    hourly = patterns.resample(candles, '1h', 12)
    ind = context.indicators(one_hour)
    stack, _ = patterns.ema_stack(hourly)
    atr_1h = patterns.indicators(hourly).atr
    out = pd.DataFrame(index=index)
    out['ret_4h'] = logc.diff(48)
    out['adx_1h'] = ind.adx.reindex(index, method='ffill').to_numpy()
    out['ema_stack_1h'] = stack.reindex(index, method='ffill')
    out['atr_pct_1h'] = atr_1h.rolling(720, min_periods=168).rank(pct=True).reindex(index, method='ffill')
    returns = np.log(hourly.close).diff()
    sigma = np.sqrt((returns**2).ewm(alpha=1-patterns.EWMA_LAMBDA, adjust=False, min_periods=24).mean())
    opens = candles.set_index('event_time').open
    start = (index-pd.Timedelta(minutes=5)).floor('4h')
    width = np.exp(patterns.Z80*sigma.reindex(start, method='ffill').to_numpy()*2.0)
    anchor = opens.reindex(start).to_numpy()
    out['band4_high'], out['band4_low'] = anchor*width, anchor/width
    bars = patterns.resample(candles, '15min', 3)[['open', 'high', 'low', 'close', 'volume']]
    out = bars.join(out, how='inner')
    out['regime'] = regimes(out)    # like patterns.regime: missing inputs fall through to RANGE (styles check warm-up)
    out['atr_1h'] = atr_1h.reindex(out.index, method='ffill')
    out['band4_mid'] = (out.band4_high+out.band4_low)/2
    out['range_pos'] = div(out.close-out.band4_low, out.band4_high-out.band4_low)
    return out.replace([np.inf, -np.inf], np.nan)


def daily_bars(candles):
    """Completed UTC days (288 five-minute bars), indexed by the day's close (next midnight)."""
    candles = candles_of(candles)
    return context.timeframe_bars(candles, '1D', 288)
