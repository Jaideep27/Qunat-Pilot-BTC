"""Pattern features for the rule engine: moves, flow, positioning, candle anatomy, VWAP, EMA stacks, squeezes,
volatility bands, divergences and support/resistance events, one row per completed 5-minute bar.

Extends `context.context_frame` (same index, same causality rules): every value at a row uses only data
known at that bar's close, levels are the ones known at the previous 15-minute close, and a missing
input stays missing. See docs/superpowers/specs/2026-10-21-rule-engine-design.md, section 3.1.
"""
from __future__ import annotations

from functools import lru_cache
import warnings

import numpy as np
import pandas as pd

from .context import confirmed_swings, context_frame, indicators, nearest_levels, _prior_period
from .data import AGES
from .features import div, wilder

Z80 = 1.2816
EWMA_LAMBDA = .94
RETEST_BARS = 8


def _candles(frames):
    candles = frames['candles'].copy()
    for column in ('event_time', 'available_at'):
        candles[column] = pd.to_datetime(candles[column], utc=True)
    candles = candles.sort_values('event_time').drop_duplicates('event_time')
    for column in ('open', 'high', 'low', 'close', 'volume', 'taker_buy_volume'):
        candles[column] = candles[column].astype(float)
    return candles


def resample(candles, rule, count):
    """Completed bars of `rule` (with taker volume), indexed by their close."""
    frame = candles.set_index('event_time')[['open', 'high', 'low', 'close', 'volume', 'taker_buy_volume']]
    grouped = frame.resample(rule, closed='left', label='left')
    bars = grouped.agg(dict(open='first', high='max', low='min', close='last', volume='sum', taker_buy_volume='sum'))
    bars = bars[grouped.close.count() == count]
    bars.index = bars.index+pd.Timedelta(rule)
    return bars


def _zscore(x, window, lag, min_periods):
    past = x.shift(lag)
    return div(x-past.rolling(window, min_periods=min_periods).mean(), past.rolling(window, min_periods=min_periods).std())


def funding_z(funding, window=90):
    """Each settlement against the previous `window` settlements (mean and std exclude it)."""
    rates = funding.sort_values('event_time').funding_rate.astype(float).reset_index(drop=True)
    past = rates.shift(1).rolling(window, min_periods=30)
    return div(rates-past.mean(), past.std())


def vwap(bars):
    """Volume-weighted average price anchored at 00:00 UTC, with its volume-weighted standard deviation."""
    typical = (bars.high+bars.low+bars.close)/3
    day = (bars.index-pd.Timedelta(minutes=5)).floor('D')
    volume = bars.volume.where(bars.volume > 0, 0.0)
    cum_v = volume.groupby(day).cumsum()
    centre = div((typical*volume).groupby(day).cumsum(), cum_v)
    var = div((typical**2*volume).groupby(day).cumsum(), cum_v)-centre**2
    sd = np.sqrt(var.where(var > (1e-10*centre)**2))
    return centre, sd


def ema_stack(bars):
    c = bars.close
    e = {n: c.ewm(span=n, adjust=False, min_periods=n).mean() for n in (9, 21, 50, 200)}
    up = (e[9] > e[21]) & (e[21] > e[50]) & (c > e[200])
    down = (e[9] < e[21]) & (e[21] < e[50]) & (c < e[200])
    stack = pd.Series(np.where(up, 1.0, np.where(down, -1.0, 0.0)), index=bars.index).where(e[200].notna())
    return stack, e


def squeeze(bars, n=20, k_bb=2.0, k_kc=1.5):
    """Bollinger bands inside Keltner channels; a release is the first bar after a squeeze ends."""
    c = bars.close
    mid, sd = c.rolling(n).mean(), c.rolling(n).std()
    tr = pd.concat([bars.high-bars.low, (bars.high-c.shift()).abs(), (bars.low-c.shift()).abs()], axis=1).max(axis=1)
    centre, atr = c.ewm(span=n, adjust=False, min_periods=n).mean(), wilder(tr)
    on = ((mid+k_bb*sd) < (centre+k_kc*atr)) & ((mid-k_bb*sd) > (centre-k_kc*atr))
    valid = sd.notna() & atr.notna()
    release = on.shift(1, fill_value=False) & ~on & valid
    return pd.DataFrame(dict(squeeze_on=on.where(valid).astype(float), squeeze_release=release.astype(float)),
                         index=bars.index)


def anatomy(bars):
    span = (bars.high-bars.low).replace(0, np.nan)
    top, bottom = np.maximum(bars.open, bars.close), np.minimum(bars.open, bars.close)
    return pd.DataFrame(dict(body_15m=(bars.close-bars.open).abs()/span, upper_wick_15m=(bars.high-top)/span,
                             lower_wick_15m=(bottom-bars.low)/span, close_loc_15m=(bars.close-bars.low)/span,
                             up_15m=(bars.close > bars.open).astype(float), open_15m=bars.open, high_15m=bars.high,
                             low_15m=bars.low, close_15m=bars.close), index=bars.index)


def divergence(bars, rsi, lookback=16, skip=4):
    """Bullish: a lower low than the prior window's low while RSI is higher than at that low (mirror bearish)."""
    low, high, r = bars.low.to_numpy(), bars.high.to_numpy(), rsi.to_numpy()
    n = len(bars)
    bull, bear = np.zeros(n), np.zeros(n)
    for i in range(lookback, n):
        window = slice(i-lookback, i-skip+1)
        j = window.start+int(np.nanargmin(low[window])) if np.isfinite(low[window]).any() else None
        k = window.start+int(np.nanargmax(high[window])) if np.isfinite(high[window]).any() else None
        if j is not None and low[i] < low[j] and r[i] > r[j]+3 and r[i] < 45:
            bull[i] = 1
        if k is not None and high[i] > high[k] and r[i] < r[k]-3 and r[i] > 55:
            bear[i] = 1
    valid = np.isfinite(r)
    return pd.DataFrame(dict(rsi_div_bull=np.where(valid, bull, np.nan), rsi_div_bear=np.where(valid, bear, np.nan)),
                        index=bars.index)


def level_events(o, h, l, c, support, resistance, atr, retest_bars=RETEST_BARS):
    """Sweeps, clean breaks, holds and retests of the levels known before each bar (ATR units)."""
    o, h, l, c, s, r, a = (np.asarray(x, float) for x in (o, h, l, c, support, resistance, atr))
    span = np.where(h > l, h-l, np.nan)
    body = np.abs(c-o)/span
    lower, upper = (np.minimum(o, c)-l)/span, (h-np.maximum(o, c))/span
    with np.errstate(invalid='ignore'):
        out = dict(
            sweep_low=(l < s-.1*a) & (c > s), sweep_high=(h > r+.1*a) & (c < r),
            break_down=(c < s-.3*a) & (body >= .6), break_up=(c > r+.3*a) & (body >= .6),
            hold_support=(l >= s-.1*a) & (l <= s+.15*a) & (c > s) & (lower >= .5),
            reject_resistance=(h <= r+.1*a) & (h >= r-.15*a) & (c < r) & (upper >= .5))
    n = len(c)
    broken_s, broken_r = np.full(n, np.nan), np.full(n, np.nan)
    retest_below, retest_above = np.zeros(n, bool), np.zeros(n, bool)
    last_s = last_r = None
    for i in range(n):
        if last_s is not None and i-last_s[0] <= retest_bars and not out['break_down'][i]:
            broken_s[i] = level = last_s[1]
            retest_below[i] = h[i] >= level-.15*a[i] and c[i] < level
        if last_r is not None and i-last_r[0] <= retest_bars and not out['break_up'][i]:
            broken_r[i] = level = last_r[1]
            retest_above[i] = l[i] <= level+.15*a[i] and c[i] > level
        if out['break_down'][i]:
            last_s = (i, s[i])
        if out['break_up'][i]:
            last_r = (i, r[i])
    out |= dict(retest_from_below=retest_below, retest_from_above=retest_above)
    frame = pd.DataFrame({k: np.asarray(v, float) for k, v in out.items()})
    frame['broken_support'], frame['broken_resistance'] = broken_s, broken_r
    return frame


def _levels(candles, bars, bars15, atr_1h):
    """Nearest support/resistance among 1h/4h swings and the prior day/week extremes, known at the previous
    15-minute close, and how many of those level types agree with it (within 0.25 ATR)."""
    prev = bars15.index-pd.Timedelta(minutes=15)
    price = bars15.close.shift(1).to_numpy()
    supports, resistances = [], []
    for tf in ('1h', '4h'):
        lv = nearest_levels(confirmed_swings(bars[tf].high, 'high'), confirmed_swings(bars[tf].low, 'low'), prev, price)
        supports.append(lv.support.to_numpy())
        resistances.append(lv.resistance.to_numpy())
    day = _prior_period(candles, prev, 'D')
    week = _prior_period(candles, prev, 'W')
    for extremes in (day, week):
        hi, lo = extremes[:, 0], extremes[:, 1]
        supports.append(np.where(lo < price, lo, np.nan))
        resistances.append(np.where(hi > price, hi, np.nan))
    s, r = np.vstack(supports), np.vstack(resistances)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)   # all-NaN columns: no level of any type
        support, resistance = np.nanmax(s, axis=0), np.nanmin(r, axis=0)
    a = atr_1h.reindex(prev, method='ffill').to_numpy()
    with np.errstate(invalid='ignore'):
        s_conf = np.sum(np.abs(s-support) <= .25*a, axis=0)
        r_conf = np.sum(np.abs(r-resistance) <= .25*a, axis=0)
    return pd.DataFrame(dict(support=support, resistance=resistance, support_confluence=s_conf.astype(float),
                             resistance_confluence=r_conf.astype(float), level_atr=a), index=bars15.index)


def _forward(frames, index):
    """Liquidations summed over the last 15 minutes and the latest order-book snapshot (forward-only)."""
    out = pd.DataFrame(index=index)
    liq = frames.get('liquidations')
    for side in ('long', 'short'):
        out[f'liq_{side}_usd_15m'] = np.nan
    out['liq_z_15m'] = np.nan
    if liq is not None and len(liq):
        liq = liq.assign(available_at=pd.to_datetime(liq.available_at, utc=True)).sort_values('available_at')
        minutes = pd.date_range(liq.available_at.min().floor('min'), liq.available_at.max().ceil('min'), freq='min')
        per = liq.groupby(liq.available_at.dt.ceil('min'))[['long_liq_usd', 'short_liq_usd']].sum().reindex(minutes,
                                                                                                         fill_value=0.0)
        sums = per.rolling(15, min_periods=1).sum()
        total = np.log1p(sums.long_liq_usd+sums.short_liq_usd)
        z = _zscore(total, 1440, 15, 240)
        covered = (index >= minutes[0]+pd.Timedelta(minutes=14)) & (index <= minutes[-1]+pd.Timedelta(minutes=2))
        for side in ('long', 'short'):
            out[f'liq_{side}_usd_15m'] = sums[f'{side}_liq_usd'].reindex(index, method='ffill').where(covered)
        out['liq_z_15m'] = z.reindex(index, method='ffill').where(covered)
    depth = frames.get('depth')
    for column in ('spread_bps', 'imbalance_15bps', 'depth_15bps_pct'):
        out[column] = np.nan
    if depth is not None and len(depth):
        d = depth.assign(available_at=pd.to_datetime(depth.available_at, utc=True)).sort_values('available_at')
        d = d.drop_duplicates('available_at', keep='last').set_index('available_at')
        size = d.bid_qty_15bps.astype(float)+d.ask_qty_15bps.astype(float)
        d['depth_15bps_pct'] = size.rolling('24h', min_periods=60).rank(pct=True)
        joined = pd.merge_asof(pd.DataFrame({'t': index}), d[['spread_bps', 'imbalance_15bps', 'depth_15bps_pct']]
                               .reset_index(), left_on='t', right_on='available_at', direction='backward',
                               tolerance=pd.Timedelta(minutes=3))
        for column in ('spread_bps', 'imbalance_15bps', 'depth_15bps_pct'):
            out[column] = joined[column].to_numpy(float)
    return out


def pattern_frame(frames, at=None):
    """Context columns plus every pattern feature, one row per completed 5m bar (or only the cutoffs in `at`)."""
    out = context_frame(frames)
    candles = _candles(frames)
    base = candles.set_index('available_at')
    index = out.index
    close = base.close
    logc = np.log(close)
    out['ret_15m'], out['ret_1h'], out['ret_4h'], out['ret_24h'] = (logc.diff(n) for n in (3, 12, 48, 288))
    out['move_z_15m'] = div(out.ret_15m, out.ret_15m.shift(3).rolling(288, min_periods=200).std())
    out['move_z_1h'] = div(out.ret_1h, out.ret_1h.shift(12).rolling(2016, min_periods=600).std())
    volume15 = base.volume.rolling(3).sum()
    out['volume_z_15m'] = _zscore(np.log(volume15.where(volume15 > 0)), 288, 3, 200)
    out['taker_share_15m'] = div(base.taker_buy_volume.rolling(3).sum(), volume15)
    oi = out.open_interest.where(out.open_interest > 0)
    out['oi_change_15m'] = oi.to_numpy()/oi.reindex(index-pd.Timedelta(minutes=15)).to_numpy()-1
    out['oi_z_1h'] = div(out.oi_change_1h, out.oi_change_1h.shift(12).rolling(2016, min_periods=600).std())
    move = np.sign(out.ret_1h)
    flow = np.sign(out.oi_change_1h)
    state = np.select([(move > 0) & (flow > 0), (move > 0) & (flow < 0), (move < 0) & (flow > 0), (move < 0) & (flow < 0)],
                      [1.0, 2.0, 3.0, 4.0], np.nan)
    out['oi_price_state'] = state   # 1 longs opening, 2 shorts covering, 3 shorts opening, 4 longs closing
    funding = frames.get('funding')
    out['funding_z'] = np.nan
    if funding is not None and len(funding):
        f = funding.sort_values('event_time').reset_index(drop=True)
        f = f.assign(z=funding_z(f), available_at=pd.to_datetime(f.available_at, utc=True))
        joined = pd.merge_asof(pd.DataFrame({'t': index}), f[['available_at', 'z']], left_on='t',
                               right_on='available_at', direction='backward', tolerance=pd.Timedelta(AGES['funding']))
        out['funding_z'] = joined.z.to_numpy(float)
    sentiment = frames.get('sentiment')
    out['fear_greed'] = np.nan
    if sentiment is not None and len(sentiment):
        s = sentiment.assign(available_at=pd.to_datetime(sentiment.available_at, utc=True)).sort_values('available_at')
        joined = pd.merge_asof(pd.DataFrame({'t': index}), s[['available_at', 'fear_greed']], left_on='t',
                               right_on='available_at', direction='backward', tolerance=pd.Timedelta(AGES['sentiment']))
        out['fear_greed'] = joined.fear_greed.to_numpy(float)
    centre, sd = vwap(base)
    out['vwap'], out['vwap_sd'] = centre, sd
    out['vwap_z'] = div(close-centre, sd)
    out['vwap_upper2'], out['vwap_lower2'] = centre+2*sd, centre-2*sd
    bars = {'15m': resample(candles, '15min', 3), '1h': resample(candles, '1h', 12), '4h': resample(candles, '4h', 48)}
    atr_1h = indicators(bars['1h']).atr
    for tf in ('15m', '1h'):
        stack, emas = ema_stack(bars[tf])
        out[f'ema_stack_{tf}'] = stack.reindex(index, method='ffill')
        if tf == '15m':
            out['ema21_15m'] = emas[21].reindex(index, method='ffill')
            out['ema21_dist_15m_atr'] = div(close-out.ema21_15m, out.atr_1h)
        else:
            out['ema200_slope_1h'] = div(emas[200].diff(3), atr_1h).reindex(index, method='ffill')
    out[['squeeze_on', 'squeeze_release']] = squeeze(bars['15m']).reindex(index, method='ffill')
    out['atr_pct_1h'] = atr_1h.rolling(720, min_periods=168).rank(pct=True).reindex(index, method='ffill')
    hourly = np.log(bars['1h'].close).diff()
    var = (hourly**2).ewm(alpha=1-EWMA_LAMBDA, adjust=False, min_periods=24).mean()
    sigma = np.sqrt(var)
    out['vol_forecast_1h'] = sigma.reindex(index, method='ffill')
    opens = candles.set_index('event_time').open
    for label, hours in (('1h', 1), ('4h', 4)):
        start = (index-pd.Timedelta(minutes=5)).floor(f'{hours}h')
        anchor = opens.reindex(start).to_numpy()
        s_known = sigma.reindex(start, method='ffill').to_numpy()
        width = np.exp(Z80*s_known*np.sqrt(hours))
        high, low = anchor*width, anchor/width
        name = 'band4' if hours == 4 else 'band1'
        out[f'{name}_high'], out[f'{name}_low'] = high, low
        out[f'range_pos_{label}'] = (close.to_numpy()-low)/(high-low)
    out['room_up_atr'] = div(out.band4_high-close, out.atr_1h)
    out['room_down_atr'] = div(close-out.band4_low, out.atr_1h)
    rsi15 = indicators(bars['15m']).rsi
    out[['rsi_div_bull', 'rsi_div_bear']] = divergence(bars['15m'], rsi15).reindex(index, method='ffill')
    b15 = bars['15m']
    shape = anatomy(b15)
    out[list(shape.columns)] = shape.reindex(index, method='ffill')
    levels = _levels(candles, bars, b15, atr_1h)
    events = level_events(b15.open, b15.high, b15.low, b15.close, levels.support, levels.resistance, levels.level_atr)
    events.index = b15.index
    out[list(levels.columns)] = levels.reindex(index, method='ffill')
    out[list(events.columns)] = events.reindex(index, method='ffill')
    out['bar15_close'] = pd.Series(b15.index, index=b15.index).reindex(index, method='ffill')
    forward = _forward(frames, index)
    out[list(forward.columns)] = forward
    if at is not None:
        out = out.loc[out.index.intersection(pd.DatetimeIndex(at))]
    return out.replace([np.inf, -np.inf], np.nan)


def regime(row):
    """PANIC, HIGH_VOL, TREND_UP, TREND_DOWN or RANGE, checked in that order (spec 3.3)."""
    get = lambda k: row.get(k) if row.get(k) is not None and row.get(k) == row.get(k) else None
    pct, ret4, adx, stack = get('atr_pct_1h'), get('ret_4h'), get('adx_1h'), get('ema_stack_1h')
    if pct is not None and pct >= .97 and ret4 is not None and abs(ret4) >= .04:
        return 'PANIC'
    if pct is not None and pct >= .85:
        return 'HIGH_VOL'
    if adx is not None and adx >= 25 and stack in (1, -1):
        return 'TREND_UP' if stack == 1 else 'TREND_DOWN'
    return 'RANGE'


@lru_cache(maxsize=1)
def columns():
    """Every column `pattern_frame` produces, from a small deterministic synthetic market (for validation)."""
    rng = np.random.default_rng(0)
    n = 10*288
    opens = pd.Timestamp('2026-01-05', tz='UTC')+pd.to_timedelta(np.arange(n)*5, unit='min')
    close = 80000*np.exp(np.cumsum(rng.normal(0, .0015, n)))
    open_ = np.r_[close[0], close[:-1]]
    candles = pd.DataFrame(dict(event_time=opens, available_at=opens+pd.Timedelta(minutes=5), open=open_,
                                high=np.maximum(open_, close)*1.001, low=np.minimum(open_, close)*.999, close=close,
                                volume=rng.uniform(50, 150, n), taker_buy_volume=rng.uniform(20, 80, n)))
    return tuple(pattern_frame(dict(candles=candles)).columns)
