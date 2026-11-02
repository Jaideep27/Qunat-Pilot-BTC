"""The analysis snapshot: one JSON with everything the dashboard and Claude read, rebuilt every 5 minutes.

Price, market type, indicator readings per timeframe, group summaries, support/resistance, the likely 4h range,
every strategy's live view, the economic calendar and the latest headlines. Completed bars only.
"""
from __future__ import annotations

import math

import pandas as pd
from sqlalchemy import JSON, Column, DateTime, String, delete, select
from sqlalchemy.orm import Session

from quantpilot.analysis import catalogue, readings, strategies
from quantpilot.features import patterns
from quantpilot.rules.journal import plain
from quantpilot.storage.database import Base

LOOKBACK = pd.Timedelta(days=45)
DAILY_LOOKBACK = pd.Timedelta(days=420)
KEEP = pd.Timedelta(hours=24)
REGIMES = {'PANIC': 'Panic: a crash or melt-up is under way', 'HIGH_VOL': 'Wild and choppy (high volatility)',
           'TREND_UP': 'Trending up (intraday)', 'TREND_DOWN': 'Trending down (intraday)',
           'RANGE': 'Moving sideways (no strong intraday trend)'}
_cache = {}


class AnalysisSnapshot(Base):
    __tablename__ = 'analysis_snapshots'
    id = Column(String(40), primary_key=True)
    built_at = Column(DateTime(timezone=True), nullable=False, index=True)
    payload = Column(JSON, nullable=False)


def _num(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _daily_candles(engine, now):
    day = pd.Timestamp(now).floor('D')
    if _cache.get('day') != day:
        from quantpilot.research.event_study import load_market
        candles = load_market(engine, day-DAILY_LOOKBACK, pd.Timestamp(now)+pd.Timedelta(minutes=1), families=())['candles']
        _cache.update(day=day, daily_candles=candles)
    c = _cache['daily_candles']
    return c[pd.to_datetime(c.available_at, utc=True) <= now]


def levels(row, price):
    """Support/resistance and reference prices from the latest pattern-frame row, with distance from price."""
    names = [('resistance_4h', '4h resistance', 'resistance'), ('resistance_1h', '1h resistance', 'resistance'),
             ('prior_day_high', 'Yesterday’s high', 'reference'), ('prior_week_high', 'Last week’s high', 'reference'),
             ('vwap_upper2', 'VWAP +2 SD', 'reference'), ('vwap', 'VWAP (today’s average price)', 'reference'),
             ('vwap_lower2', 'VWAP −2 SD', 'reference'), ('prior_day_low', 'Yesterday’s low', 'reference'),
             ('prior_week_low', 'Last week’s low', 'reference'), ('support_1h', '1h support', 'support'),
             ('support_4h', '4h support', 'support')]
    out = []
    for key, label, kind in names:
        value = _num(row.get(key))
        if value is None or not price:
            continue
        out.append(dict(id=key, label=label, kind=kind, price=value, distance=value/price-1))
    return sorted(out, key=lambda r: -r['price'])


def _forecast_4h():
    try:
        from quantpilot.research.forecaster import forecasts
        from quantpilot.storage.database import database
        rows = [r for r in forecasts(database(), 20) if r.get('horizon') == '4h']
        return rows[0] if rows else None
    except Exception:
        return None


def _calendar(now):
    try:
        from quantpilot.data.macro import snapshot as macro_snapshot
        from quantpilot.storage.database import database
        data = macro_snapshot(database(), now.to_pydatetime())
        upcoming = sorted((e for e in data['upcoming'].values() if e), key=lambda e: e['scheduled_at'])
        return dict(upcoming=[dict(label=e['label'], kind=e['kind'], at=e['scheduled_at']) for e in upcoming],
                    recent=[dict(label=e['label'], kind=e['kind'], at=e['scheduled_at'], value=e.get('value'))
                            for e in data['recent'][:3]])
    except Exception:
        return dict(upcoming=[], recent=[])


def _headlines(limit=6):
    try:
        from quantpilot.ai.news import snapshot as news_snapshot
        from quantpilot.api.services import _compact_news
        from quantpilot.storage.database import database
        data = _compact_news(news_snapshot(database(), 20))
        return dict(items=data['items'][:limit], analyses=data['analyses'][:limit])
    except Exception:
        return dict(items=[], analyses=[])


def _macro_events():
    try:
        from quantpilot.data import macro
        from quantpilot.storage.database import database
        return macro.events(database())
    except Exception:
        return None


def build(engine, now=None, frames=None, daily_candles=None, side_data=True):
    """The snapshot at `now` (default: the latest data). `frames`/`daily_candles` can be passed in for tests."""
    now = pd.Timestamp(now or pd.Timestamp.now(tz='UTC'))
    if frames is None:
        from quantpilot.research.event_study import load_market
        frames = load_market(engine, now-LOOKBACK, now+pd.Timedelta(minutes=1))
    frames = {k: v[pd.to_datetime(v.available_at, utc=True) <= now] for k, v in frames.items() if v is not None}
    candles = frames['candles']
    if daily_candles is None:
        daily_candles = _daily_candles(engine, now)
    from quantpilot.strategies import market
    pf = patterns.pattern_frame(frames)
    sf = market.style_frame(candles)
    daily = market.daily_bars(daily_candles)
    row = pf.iloc[-1].to_dict() if len(pf) else {}
    price = _num(row.get('close'))
    closes = pd.Series(candles.close.astype(float).to_numpy(), index=pd.to_datetime(candles.available_at, utc=True))
    day_ago = closes[closes.index <= closes.index[-1]-pd.Timedelta(hours=24)]
    last_day = candles[pd.to_datetime(candles.available_at, utc=True) > closes.index[-1]-pd.Timedelta(hours=24)]
    by_tf = {tf: readings.timeframe_readings(candles, tf) for tf in ('15m', '1h', '4h')}
    by_tf['1d'] = readings.timeframe_readings(daily_candles, '1d')
    mkt = readings.market_readings(pf)
    summary = {tf: readings.group_summary(v['items']+mkt['items']) for tf, v in by_tf.items()}
    regime = patterns.regime(row) if row else None
    macro_events = _macro_events() if side_data else None
    views = strategies.all_views(sf, daily, frames.get('funding'), pf, macro_events)
    forecast = _forecast_4h() if side_data else None
    band = dict(low=_num(row.get('band4_low')), high=_num(row.get('band4_high')), position=_num(row.get('range_pos_4h')))
    out = dict(
        built_at=now.isoformat(), data_time=closes.index[-1].isoformat(), symbol='BTCUSDT perpetual (Binance)',
        price=dict(last=price, change_24h=None if not len(day_ago) or not price else price/float(day_ago.iloc[-1])-1,
                   high_24h=_num(last_day.high.astype(float).max()), low_24h=_num(last_day.low.astype(float).min()),
                   volume_24h=_num(last_day.volume.astype(float).sum())),
        regime=dict(id=regime, text=REGIMES.get(regime, '—')),
        timeframes=by_tf, market=mkt, summary=summary, levels=levels(row, price),
        range_4h=dict(band=band, forecast=None if not forecast else dict(
            low=None if forecast.get('return_q10') is None or not price else price*(1+forecast['return_q10']),
            high=None if forecast.get('return_q90') is None or not price else price*(1+forecast['return_q90']),
            probability_up=forecast.get('probability_up'), status=forecast.get('status'), cutoff=forecast.get('cutoff'))),
        strategies=views, calendar=_calendar(now) if side_data else dict(upcoming=[], recent=[]),
        headlines=_headlines() if side_data else dict(items=[], analyses=[]),
        note='Analysis only: nothing here places or recommends a trade. Not financial advice.')
    return plain(out)


def save(engine, snap):
    built = pd.Timestamp(snap['built_at'])
    with Session(engine) as session, session.begin():
        session.merge(AnalysisSnapshot(id=built.isoformat(), built_at=built.to_pydatetime(), payload=snap))
        session.execute(delete(AnalysisSnapshot).where(AnalysisSnapshot.built_at < (built-KEEP).to_pydatetime()))


def latest(engine):
    with Session(engine) as session:
        row = session.scalars(select(AnalysisSnapshot).order_by(AnalysisSnapshot.built_at.desc()).limit(1)).first()
        return None if row is None else row.payload


def compact(snap):
    """The snapshot without sparklines, curves and checklists' detail: what Claude reads (a few KB)."""
    def items(rows):
        return [dict(name=catalogue.BY_ID[r['id']]['name'], value=r['value'], reading=r['label'], meaning=r['sentence'])
                for r in rows if r.get('value') is not None]
    styles = [dict(name=s['name'], status=s['status'], says=s['says'],
                   history=None if not s.get('history') else dict(verdict=s['history']['verdict'],
                                                                  account_return=s['history']['account_return'],
                                                                  buy_and_hold=s['history']['buy_and_hold']))
              for s in snap['strategies']['styles']]
    firing = snap['strategies']['rulebook']['firing']
    return dict(built_at=snap['built_at'], price=snap['price'], market_type=snap['regime']['text'],
                timeframes={tf: dict(time=v['time'], indicators=items(v['items'])) for tf, v in snap['timeframes'].items()},
                live=items(snap['market']['items']),
                group_summary={tf: {g: s['line'] for g, s in v.items()} for tf, v in snap['summary'].items()},
                levels=[dict(label=l['label'], price=round(l['price'], 1), distance=round(l['distance'], 4)) for l in snap['levels']],
                likely_range_4h=snap['range_4h'], strategies=styles,
                rulebook_firing=[f"{f['id']} {f['name']} ({f['side']})" for f in firing],
                calendar=snap['calendar'], headlines=[dict(title=h['title'], source=h['source'], at=h['available_at'])
                                                      for h in snap['headlines']['items']],
                news_analysis=[dict(summary=a['summary'], sentiment=a['sentiment']) for a in snap['headlines']['analyses']])
