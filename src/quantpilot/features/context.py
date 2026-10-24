"""MarketContext: one causal, multi-timeframe snapshot per completed 5-minute bar.

Rows are indexed by the bar's close (the instant it becomes known). Higher timeframes use completed
bars only; swing levels count only once confirmed; every external family is joined backward with its
availability tolerance. The snapshot is context for trade plans and policies, never a direction call.
See docs/design/specs/2026-10-20-trading-agent-step-a-design.md.
"""
from __future__ import annotations

from datetime import datetime
import math

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from quantpilot.data.archives import AGES
from quantpilot.features.core import div, wilder

TIMEFRAMES = {'5m': ('5min', 1), '15m': ('15min', 3), '1h': ('1h', 12), '4h': ('4h', 48)}
STRUCTURE = ('15m', '1h', '4h')
SWINGS_KEPT = 12
BLACKOUT = (pd.Timedelta(minutes=30), pd.Timedelta(minutes=15))
STALE = {'mark': 600, 'funding': 9*3600}
SCHEMA_VERSION = 1


def indicators(bars):
    """RSI, ADX, ATR, EMA distances, MACD histogram and trend: the same definitions as `features.technical`."""
    h, l, c = bars.high, bars.low, bars.close
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr)
    delta = c.diff()
    up, down = h.diff(), -l.diff()
    plus = wilder(up.where((up > down) & (up > 0), 0))
    minus = wilder(down.where((down > up) & (down > 0), 0))
    ema21, ema50 = (c.ewm(span=n, adjust=False, min_periods=n).mean() for n in (21, 50))
    macd = c.ewm(span=12, adjust=False).mean()-c.ewm(span=26, adjust=False).mean()
    return pd.DataFrame(dict(
        rsi=100-100/(1+div(wilder(delta.clip(lower=0)), wilder(-delta.clip(upper=0)))),
        adx=100*wilder(div((plus-minus).abs(), plus+minus)), atr=atr,
        ema21_dist=div(c-ema21, atr), ema50_dist=div(c-ema50, atr),
        macd_hist=div(macd-macd.ewm(span=9, adjust=False).mean(), atr),
        trend=np.sign(c-ema50).where(ema50.notna())), index=bars.index)


def timeframe_bars(candles, rule, count):
    """Completed bars of a higher timeframe, indexed by their close; partial periods are dropped."""
    frame = candles.set_index('event_time')[['open', 'high', 'low', 'close', 'volume']]
    grouped = frame.resample(rule, closed='left', label='left')
    bars = grouped.agg(dict(open='first', high='max', low='min', close='last', volume='sum'))
    bars = bars[grouped.close.count() == count]
    bars.index = bars.index+pd.Timedelta(rule)
    return bars


def confirmed_swings(series, kind):
    """A centered 5-bar extreme, emitted 2 bars after it forms (the bar at which it is known)."""
    window = series.rolling(5).max() if kind == 'high' else series.rolling(5).min()
    candidate = series.shift(2)
    return candidate[candidate == window].dropna()


def nearest_levels(highs, lows, at, price, n=SWINGS_KEPT):
    """Per cutoff: lowest of the last `n` confirmed swing highs above the price (resistance) and the highest
    of the last `n` confirmed swing lows below it (support)."""
    at = pd.DatetimeIndex(at)
    price = np.asarray(price, float)
    out = {}
    for name, swings, above in (('resistance', highs, True), ('support', lows, False)):
        times = pd.DatetimeIndex(swings.index).asi8
        values = swings.to_numpy(float)
        count = np.searchsorted(times, at.asi8, side='right')
        positions = count[:, None]-1-np.arange(n)[None, :]
        valid = positions >= 0
        picked = np.where(valid, values[np.clip(positions, 0, None)] if len(values) else np.nan, np.nan)
        if above:
            candidates = np.where(picked > price[:, None], picked, np.inf)
            best = candidates.min(axis=1)
        else:
            candidates = np.where(picked < price[:, None], picked, -np.inf)
            best = candidates.max(axis=1)
        out[name] = np.where(np.isfinite(best), best, np.nan)
    return pd.DataFrame(out, index=at)[['resistance', 'support']]


def _join(index, frame, columns, tolerance):
    """Backward join of an external family; also returns the age (seconds) of the matched observation."""
    left = pd.DataFrame({'cutoff': index})
    if frame is None or frame.empty:
        return pd.DataFrame({**{c: np.nan for c in columns}, 'age': np.nan}, index=index)
    right = frame[['available_at', *columns]].dropna().sort_values('available_at')
    right = right.assign(available_at=pd.to_datetime(right.available_at, utc=True))
    joined = pd.merge_asof(left, right, left_on='cutoff', right_on='available_at', direction='backward',
                           tolerance=pd.Timedelta(tolerance))
    joined['age'] = (joined.cutoff-joined.available_at).dt.total_seconds()
    return joined.set_index(pd.DatetimeIndex(index))[[*columns, 'age']]


def _prior_period(candles, index, freq):
    opens = candles.set_index('event_time')
    period = opens.index.floor('D') if freq == 'D' else (opens.index.floor('D')-pd.to_timedelta(opens.index.weekday, 'D'))
    grouped = opens.groupby(period).agg(dict(high='max', low='min'))
    prior = grouped.shift(1)
    row_open = pd.DatetimeIndex(index)-pd.Timedelta(minutes=5)
    row_period = row_open.floor('D') if freq == 'D' else (row_open.floor('D')-pd.to_timedelta(row_open.weekday, 'D'))
    return prior.reindex(row_period).to_numpy()


def context_frame(frames, at=None):
    """One row per completed 5-minute bar (or only the cutoffs in `at`), using data known at that close."""
    candles = frames['candles'].copy()
    for column in ('event_time', 'available_at'):
        candles[column] = pd.to_datetime(candles[column], utc=True)
    candles = candles.sort_values('event_time').drop_duplicates('event_time')
    for column in ('open', 'high', 'low', 'close', 'volume', 'taker_buy_volume'):
        candles[column] = candles[column].astype(float)
    base = candles.set_index('available_at')
    index = base.index
    out = pd.DataFrame(index=index)
    out['close'], out['high'], out['low'], out['volume'] = base.close, base.high, base.low, base.volume
    out['relative_volume'] = div(base.volume, base.volume.rolling(288, min_periods=12).mean())
    out['taker_share_1h'] = div(base.taker_buy_volume.rolling(12).sum(), base.volume.rolling(12).sum())
    bars = {}
    for tf, (rule, count) in TIMEFRAMES.items():
        tf_bars = base[['open', 'high', 'low', 'close', 'volume']] if tf == '5m' else timeframe_bars(candles, rule, count)
        bars[tf] = tf_bars
        values = indicators(tf_bars).reindex(index, method='ffill') if tf != '5m' else indicators(tf_bars)
        for column in values:
            out[f'{column}_{tf}'] = values[column].to_numpy()
    # Expected 24h move: std of the last 168 hourly log returns, scaled to a day (completed hours only).
    hourly_vol = np.log(bars['1h'].close).diff().rolling(168).std()*np.sqrt(24)
    out['volatility_24h'] = hourly_vol.reindex(index, method='ffill').to_numpy()
    mark = _join(index, frames.get('mark'), ['mark_price'], AGES['mark'])
    idx = _join(index, frames.get('index'), ['index_price'], AGES['index'])
    positioning = _join(index, frames.get('positioning'),
                        ['open_interest', 'global_ratio', 'top_account_ratio', 'top_position_ratio'], AGES['positioning'])
    funding = _join(index, frames.get('funding'), ['funding_rate'], AGES['funding'])
    out['mark'], out['mark_age'] = mark.mark_price, mark.age
    out['index'] = idx.index_price
    out['premium'] = div(out.mark, out['index'])-1
    for column in ('open_interest', 'global_ratio', 'top_account_ratio', 'top_position_ratio'):
        out[column] = positioning[column]
    out['positioning_age'] = positioning.age
    out['funding_rate'], out['funding_age'] = funding.funding_rate, funding.age
    oi = out.open_interest.where(out.open_interest > 0)
    for label, lag in (('1h', '1h'), ('24h', '24h')):
        out[f'oi_change_{label}'] = oi.to_numpy()/oi.reindex(index-pd.Timedelta(lag)).to_numpy()-1
    out[['prior_day_high', 'prior_day_low']] = _prior_period(candles, index, 'D')
    out[['prior_week_high', 'prior_week_low']] = _prior_period(candles, index, 'W')
    if at is not None:
        out = out.loc[out.index.intersection(pd.DatetimeIndex(at))]
    for tf in STRUCTURE:
        levels = nearest_levels(confirmed_swings(bars[tf].high, 'high'), confirmed_swings(bars[tf].low, 'low'),
                                out.index, out.close)
        atr = out[f'atr_{tf}']
        out[f'resistance_{tf}'], out[f'support_{tf}'] = levels.resistance.to_numpy(), levels.support.to_numpy()
        out[f'resistance_{tf}_atr'] = div(out[f'resistance_{tf}']-out.close, atr)
        out[f'support_{tf}_atr'] = div(out.close-out[f'support_{tf}'], atr)
    return out.replace([np.inf, -np.inf], np.nan)


def macro_state(events, cutoff):
    """Next known release and whether `cutoff` falls in its entry blackout (30 min before to 15 min after)."""
    cutoff = pd.Timestamp(cutoff)
    timed = sorted(((pd.Timestamp(e['scheduled_at']), e) for e in events or [] if e.get('scheduled_at')),
                   key=lambda pair: pair[0])
    blackout = any(t-BLACKOUT[0] <= cutoff <= t+BLACKOUT[1] for t, _ in timed)
    upcoming = next(((t, e) for t, e in timed if t >= cutoff), None)
    recent = next(((t, e) for t, e in reversed(timed) if t < cutoff), None)
    describe = lambda pair: None if pair is None else dict(kind=pair[1].get('kind'), label=pair[1].get('label'),
                                                           scheduled_at=pair[0].isoformat(),
                                                           hours=round((pair[0]-cutoff).total_seconds()/3600, 2))
    return dict(blackout=blackout, next=describe(upcoming), last=describe(recent))


def news_state(items, cutoff, limit=3):
    cutoff = pd.Timestamp(cutoff)
    known = sorted((i for i in items or [] if i.get('available_at') and pd.Timestamp(i['available_at']) <= cutoff),
                   key=lambda i: i['available_at'], reverse=True)
    recent = [i for i in known if pd.Timestamp(i['available_at']) > cutoff-pd.Timedelta(hours=24)]
    return dict(count_24h=len(recent), latest=[dict(title=i.get('title'), source=i.get('source'),
                                                    available_at=i['available_at']) for i in known[:limit]])


def forecast_state(forecasts, cutoff):
    """Latest shadow forecast per horizon recorded at or before the cutoff."""
    cutoff, latest = pd.Timestamp(cutoff), {}
    for row in sorted(forecasts or [], key=lambda r: r['cutoff']):
        made = pd.Timestamp(row['cutoff'])
        if made <= cutoff:
            latest[row['horizon']] = dict(
                made_at=row['cutoff'], age_minutes=round((cutoff-made).total_seconds()/60, 1),
                probability_up=row.get('probability_up'), base_rate=row.get('base_rate'), status=row.get('status'),
                return_q10=row.get('return_q10'), return_q90=row.get('return_q90'),
                range_status=row.get('range_status'), model_version=row.get('model_version'))
    return latest


def _clean(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


class MarketContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: int = SCHEMA_VERSION
    cutoff: datetime
    price: float
    mark: float | None
    index: float | None
    premium: float | None
    volume: float | None
    relative_volume: float | None
    taker_share_1h: float | None
    open_interest: float | None
    oi_change_1h: float | None
    oi_change_24h: float | None
    global_ratio: float | None
    top_account_ratio: float | None
    top_position_ratio: float | None
    funding_rate: float | None
    volatility_24h: float | None
    timeframes: dict[str, dict[str, float | None]]
    levels: dict[str, dict[str, float | None]]
    forecasts: dict[str, dict]
    macro: dict
    news: dict
    account: dict | None
    availability: dict[str, float | None]
    blocking: list[str]


def snapshot_from_row(row, forecasts=None, macro_events=None, news_items=None, account=None):
    cutoff = pd.Timestamp(row.name)
    get = lambda key: _clean(row.get(key))
    blocking = [name for name, limit in STALE.items()
                if get(f'{name}_age') is None or get(f'{name}_age') > limit]
    if get('close') is None:
        blocking.insert(0, 'price')
    timeframes = {tf: {k: get(f'{k}_{tf}') for k in ('rsi', 'adx', 'atr', 'ema21_dist', 'ema50_dist', 'macd_hist',
                                                        'trend')} for tf in TIMEFRAMES}
    levels = {tf: {k: get(f'{k}_{tf}{suffix}') for k, suffix in (('resistance', ''), ('support', ''))}
              | {f'{k}_atr': get(f'{k}_{tf}_atr') for k in ('resistance', 'support')} for tf in STRUCTURE}
    levels['prior_day'] = dict(high=get('prior_day_high'), low=get('prior_day_low'))
    levels['prior_week'] = dict(high=get('prior_week_high'), low=get('prior_week_low'))
    return MarketContext(
        cutoff=cutoff.to_pydatetime(), price=get('close') or 0.0, mark=get('mark'), index=get('index'),
        premium=get('premium'), volume=get('volume'), relative_volume=get('relative_volume'),
        taker_share_1h=get('taker_share_1h'), open_interest=get('open_interest'), oi_change_1h=get('oi_change_1h'),
        oi_change_24h=get('oi_change_24h'), global_ratio=get('global_ratio'),
        top_account_ratio=get('top_account_ratio'), top_position_ratio=get('top_position_ratio'),
        funding_rate=get('funding_rate'), volatility_24h=get('volatility_24h'), timeframes=timeframes, levels=levels,
        forecasts=forecast_state(forecasts, cutoff), macro=macro_state(macro_events, cutoff),
        news=news_state(news_items, cutoff), account=account,
        availability=dict(mark_age=get('mark_age'), funding_age=get('funding_age'),
                          positioning_age=get('positioning_age')), blocking=blocking)


def snapshot(frame, cutoff, forecasts=None, macro_events=None, news_items=None, account=None):
    """The context at `cutoff`: the last completed bar at or before it."""
    rows = frame.loc[:pd.Timestamp(cutoff)]
    if rows.empty:
        raise ValueError('no completed bar at or before the cutoff')
    return snapshot_from_row(rows.iloc[-1], forecasts, macro_events, news_items, account)
