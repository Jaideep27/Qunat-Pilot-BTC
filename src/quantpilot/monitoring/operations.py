"""Read-only readiness for the analysis app: the database answers, candles are fresh and the snapshot is current."""
import pandas as pd
from sqlalchemy import select
from quantpilot.storage.market import MarketCandle, MarketFeed, PERP
from quantpilot.data.archives import utc

CANDLE_MAX_AGE = 600          # a completed 5m candle at most 10 minutes old
SNAPSHOT_MAX_AGE = 900        # the analysis snapshot is rebuilt every 5 minutes


def report(market_engine, now=None):
    now = utc(now or pd.Timestamp.now(tz='UTC'))
    age = lambda value: max(0., (now-utc(value)).total_seconds()) if value else None
    with market_engine.connect() as connection:
        # Newest completed 5m candle through the (market, interval, event_time) index; a max() over available_at
        # scanned the whole table and took 5-50 s when the laptop was short of memory.
        last_bar = connection.execute(select(MarketCandle.available_at).where(
            MarketCandle.market == PERP, MarketCandle.interval == '5m', MarketCandle.complete.is_(True),
            MarketCandle.available_at <= now.to_pydatetime()).order_by(MarketCandle.event_time.desc()).limit(1)).scalar()
        feeds = {row.id: row for row in connection.execute(select(MarketFeed.__table__).where(
            MarketFeed.id.in_(['live:collector', 'analysis:snapshot'])))}
    candle_age = age(last_bar)
    snapshot = feeds.get('analysis:snapshot')
    snapshot_at = (snapshot.state or {}).get('built_at') if snapshot else None
    snapshot_age = age(snapshot_at)
    collector = feeds.get('live:collector')
    checks = {
        'database': dict(ok=True, status='connected'),
        'completed_candles': dict(ok=candle_age is not None and candle_age <= CANDLE_MAX_AGE, age_seconds=candle_age,
                                  status='missing' if candle_age is None else
                                  'fresh' if candle_age <= CANDLE_MAX_AGE else 'stale'),
        # Informational: a just-started service has no snapshot for a few minutes, so it does not gate readiness.
        'analysis_snapshot': dict(ok=True, age_seconds=snapshot_age, built_at=snapshot_at,
                                  status='missing' if snapshot_age is None else
                                  'fresh' if snapshot_age <= SNAPSHOT_MAX_AGE else 'stale')}
    return dict(ready=all(c['ok'] for c in checks.values()), checked_at=now.isoformat(), checks=checks,
                collector=None if collector is None else dict(status=(collector.state or {}).get('status'),
                                                                age_seconds=age(collector.updated_at),
                                                                poll_interval_seconds=(collector.state or {}).get('poll_interval_seconds')))
