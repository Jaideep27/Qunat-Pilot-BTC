"""Real PostgreSQL verification in a disposable database: migrations, table set-up, market storage, the collector,
readiness and the analysis tables. Writes .verification/postgres-platform.json."""
import json
from pathlib import Path
import uuid

import httpx
import pandas as pd
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text

from quantpilot.analysis import ai, snapshot
from quantpilot.config import settings
from quantpilot.data.collector import collect_once
from quantpilot.monitoring.operations import report
from quantpilot.storage.database import Base, database, initialize
from quantpilot.storage.market import MarketCandle, MarketObservation, candles, feed_status, save_frame, summary


def klines(stamps):
    """Binance kline rows for 5m bars opening at `stamps`; repeated once, as some provider batches are."""
    raw = [[int(t.timestamp()*1000), '100', '102', '99', '101', '10', int((t+pd.Timedelta(minutes=5)).timestamp()*1000)-1,
            '1010', 20, '6', '606', '0'] for t in stamps]
    return raw+raw


def main():
    Path('.verification').mkdir(exist_ok=True)
    owner = database(settings.market_database_url)
    assert owner.dialect.name == 'postgresql'
    name = 'quantpilot_verify_'+uuid.uuid4().hex[:12]
    admin = create_engine(owner.url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(owner.url.set(database=name))
    checks = []
    try:
        # 1. Every migration runs on an empty database.
        original_url, isolated_url = settings.database_url, engine.url.render_as_string(hide_password=False)
        try:
            settings.database_url = isolated_url
            command.upgrade(Config('alembic.ini'), 'head')
        finally:
            database(isolated_url).dispose()
            settings.database_url = original_url
        checks.append('fresh Alembic migration to head')

        # 2. Table set-up creates every table with row-level security, and a second run changes nothing.
        initialize(engine)
        initialize(engine)
        with engine.connect() as connection:
            missing = connection.execute(text("SELECT relname FROM pg_class WHERE relkind='r' AND NOT relrowsecurity "
                                              "AND relname = ANY(:names)"), dict(names=list(Base.metadata.tables))).scalars().all()
        assert missing == [], missing
        checks.append('every table created with row-level security; repeated set-up is a no-op')

        # 3. The collector upserts idempotently, keeps the forming bar apart and looks only at 5m candles.
        now = pd.Timestamp.now(tz='UTC').floor('5min')
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=klines([now-pd.Timedelta(minutes=5), now])))) as client:
            collect_once(engine, client)
            collect_once(engine, client)
        with engine.connect() as connection:
            assert connection.execute(select(func.count()).select_from(MarketCandle)).scalar() == 2
            assert connection.execute(select(func.count()).select_from(MarketCandle).where(MarketCandle.complete.is_(False))).scalar() == 1
            row = dict(connection.execute(select(MarketCandle.__table__).where(MarketCandle.complete.is_(True))).mappings().one())
        with engine.begin() as connection:     # a newer 1m candle must not hide missing 5m bars
            connection.execute(MarketCandle.__table__.insert(), [dict(row, interval='1m', event_time=(now+pd.Timedelta(hours=1)).to_pydatetime())])
        starts = []
        def recording(request):
            starts.append(int(request.url.params['startTime']))
            return httpx.Response(200, json=klines([now]))
        with httpx.Client(transport=httpx.MockTransport(recording)) as client:
            collect_once(engine, client)
        assert starts and starts[0] <= int(now.timestamp()*1000)
        checks.append('collector upserts idempotently, separates the forming bar and resumes from the last 5m candle')

        # 4. Observations keep a NULL quality flag; the completed-candle query and readiness answer.
        frame = pd.DataFrame([dict(event_time=now, available_at=now, received_at=now, source='fixture:postgres',
                                   open_interest=1, global_ratio=1, top_account_ratio=float('nan'), top_position_ratio=1)])
        save_frame(engine, 'positioning', frame)
        save_frame(engine, 'positioning', frame)
        with engine.connect() as connection:
            obs = connection.execute(select(MarketObservation.__table__)).mappings().one()
        assert obs['values']['top_account_ratio'] is None and not obs['valid']
        result = candles(engine, limit=10)
        assert len(result) == 1 and result[0]['close'] == 101
        feed_status(engine, 'analysis:snapshot', dict(status='healthy', built_at=pd.Timestamp.now(tz='UTC').isoformat()))
        ready = report(engine)
        assert ready['ready'] and ready['checks']['analysis_snapshot']['status'] == 'fresh'
        checks.append('JSON NULL quality flag, completed-candle query and readiness')

        # 5. Analysis snapshots, briefs and explanations round-trip as JSON.
        snap = dict(built_at=pd.Timestamp.now(tz='UTC').isoformat(), price=dict(last=101.0), note='fixture')
        snapshot.save(engine, snap)
        assert snapshot.latest(engine)['built_at'] == snap['built_at']
        ai.write_brief(engine, dict(snap, regime=dict(text='x'), timeframes={}, market=dict(items=[]), summary={},
                                    levels=[], range_4h={}, strategies=dict(styles=[], rulebook=dict(firing=[])),
                                    calendar={}, headlines=dict(items=[], analyses=[])),
                       call=lambda *_: dict(headline='h', overall='quiet', summary='s', trend='t', momentum='m',
                                            volatility='v', volume_flow='f', positioning='p', strategies='x',
                                            news_calendar='n', levels_to_watch=[], risks=[]))
        assert ai.briefs(engine, 1)[0]['status'] == 'ok'
        checks.append('analysis snapshot and Claude brief tables round-trip')

        output = dict(status='passed', database='PostgreSQL', disposable_database=name, checks=checks,
                      live_database_counts=summary(owner))
        Path('.verification/postgres-platform.json').write_text(json.dumps(output, indent=2, default=str), encoding='utf-8')
        print(f'Real PostgreSQL integration: {len(checks)} checks passed; isolated test database removed on exit.')
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}"'))


if __name__ == '__main__':
    main()
