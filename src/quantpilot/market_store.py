"""Durable market history, independent of the immutable research dataset."""
from datetime import timedelta
import math
import json
from pathlib import Path
import pandas as pd
from sqlalchemy import Column, String, DateTime, Float, Boolean, JSON, Index, select, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from .store import Base, database
from .config import settings
from .data import utc, VALUES

PERP = 'BTCUSDT_PERP'
# Public symbol; retain the original storage key to preserve research provenance.
SYMBOL = 'BTCUSDT'
SPOT = 'BTCUSDT_SPOT'
REFERENCE = 'BTCUSD_REFERENCE'
TIMEFRAMES = {'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400,'1d':86400,'1w':604800}


class MarketCandle(Base):
    __tablename__ = 'market_candles'
    market = Column(String(32), primary_key=True)
    interval = Column(String(8), primary_key=True)
    event_time = Column(DateTime(timezone=True), primary_key=True)
    available_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    source = Column(String(512), nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    trade_count = Column(Float)
    taker_buy_volume = Column(Float)
    complete = Column(Boolean, nullable=False, default=True)
    __table_args__ = (Index('ix_market_candles_lookup', 'market', 'interval', 'event_time'),)


class MarketObservation(Base):
    __tablename__ = 'market_observations'
    market = Column(String(32), primary_key=True)
    family = Column(String(24), primary_key=True)
    event_time = Column(DateTime(timezone=True), primary_key=True)
    available_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False)
    source = Column(String(512), nullable=False)
    values = Column(JSON, nullable=False)
    valid = Column(Boolean, nullable=False)


class MarketFeed(Base):
    __tablename__ = 'market_feeds'
    id = Column(String(120), primary_key=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    state = Column(JSON, nullable=False)


# Forward-only research families (liquidations, order book); never part of the release audit.
FORWARD_VALUES = {'liquidations': ['long_liq_qty', 'long_liq_usd', 'short_liq_qty', 'short_liq_usd', 'liq_count'],
                  'depth': ['mid', 'spread_bps', 'bid_qty_5bps', 'ask_qty_5bps', 'imbalance_5bps',
                            'bid_qty_15bps', 'ask_qty_15bps', 'imbalance_15bps']}


def _finite(value):
    return float(value) if pd.notna(value) and math.isfinite(float(value)) else None


def _upsert(engine, table, rows, keys, condition=None):
    if not rows: return 0
    # A provider batch can repeat a timestamp. PostgreSQL cannot update the same
    # conflict key twice in one INSERT; match retry/version rules before COPY.
    unique={}
    for row in rows:
        key=tuple(row[name] for name in keys)
        previous=unique.get(key)
        if previous is None:
            unique[key]=row
            continue
        def version(value):
            return (value.get('complete',True),value.get('received_at') or value.get('updated_at'))
        if version(row)>version(previous):unique[key]=row
        elif version(row)==version(previous) and row!=previous:
            raise ValueError(f'conflicting duplicate {table.name} key: {key}')
    rows=list(unique.values())
    if engine.dialect.name=='postgresql':
        from psycopg.types.json import Json
        # COPY avoids compiling thousands of huge parameterized INSERT statements.
        columns=list(rows[0])
        quoted=','.join('"'+name+'"' for name in columns)
        updates=','.join(f'"{name}"=EXCLUDED."{name}"' for name in columns if name not in keys)
        conflict=','.join('"'+name+'"' for name in keys)
        where=' WHERE '+str(condition(pg_insert(table)).compile(dialect=engine.dialect)) if condition else ''
        raw=engine.raw_connection()
        try:
            with raw.cursor() as cursor:
                cursor.execute(f'CREATE TEMP TABLE qp_market_ingest (LIKE "{table.name}" INCLUDING DEFAULTS) ON COMMIT DROP')
                with cursor.copy(f'COPY qp_market_ingest ({quoted}) FROM STDIN') as stream:
                    for row in rows:
                        stream.write_row([Json(row[name]) if isinstance(row[name],(dict,list)) else row[name] for name in columns])
                cursor.execute(f'INSERT INTO "{table.name}" ({quoted}) SELECT {quoted} FROM qp_market_ingest WHERE true '
                    f'ON CONFLICT ({conflict}) DO UPDATE SET {updates}'+where)
            raw.commit()
        except Exception:
            raw.rollback();raise
        finally:raw.close()
        return len(rows)
    insert = pg_insert if engine.dialect.name == 'postgresql' else sqlite_insert
    with engine.begin() as connection:
        for offset in range(0, len(rows), 1000):
            stmt = insert(table).values(rows[offset:offset+1000])
            updates = {c.name:stmt.excluded[c.name] for c in table.columns if c.name not in keys}
            where = condition(stmt) if condition else None
            connection.execute(stmt.on_conflict_do_update(index_elements=keys, set_=updates, where=where))
    return len(rows)


def save_frame(engine, family, frame, market=PERP):
    """Replay-safe upsert. Invalid external values are retained as NULL plus valid=false."""
    if frame.empty: return 0
    if family not in VALUES and family not in FORWARD_VALUES and family != 'reference': raise ValueError('unsupported family')
    rows=[]
    for record in frame.to_dict('records'):
        row={key:utc(record[key]).to_pydatetime() for key in ('event_time','available_at','received_at')}
        row.update(market=market, source=str(record['source']))
        if family == 'candles':
            prices={key:_finite(record[key]) for key in ('open','high','low','close','volume')}
            if any(v is None for v in prices.values()) or min(prices[k] for k in ('open','high','low','close'))<=0:
                raise ValueError('invalid candle')
            if prices['volume']<0 or prices['high']<max(prices['open'],prices['close'],prices['low']) or prices['low']>min(prices['open'],prices['close']):
                raise ValueError('invalid OHLC range')
            row.update(prices,interval='5m',trade_count=_finite(record.get('trade_count')),
                       taker_buy_volume=_finite(record.get('taker_buy_volume')),complete=True)
        else:
            fields=VALUES.get(family) or FORWARD_VALUES.get(family) or ['price_usd']
            values={key:_finite(record[key]) for key in fields}
            row.update(family=family,values=values,valid=all(v is not None for v in values.values()))
        rows.append(row)
    table=MarketCandle.__table__ if family=='candles' else MarketObservation.__table__
    keys=['market','interval','event_time'] if family=='candles' else ['market','family','event_time']
    # Older retries cannot overwrite a newer observation.
    return _upsert(engine,table,rows,keys,lambda stmt:stmt.excluded.received_at>=table.c.received_at)


def feed_status(engine, key, state):
    _upsert(engine,MarketFeed.__table__,[dict(id=key,updated_at=pd.Timestamp.now(tz='UTC').to_pydatetime(),state=state)],['id'])


def import_parquet(root, engine=None):
    engine=engine or database()
    result={}
    for family in VALUES:
        path=Path(root)/f'{family}.parquet'
        if not path.exists(): continue
        frame=pd.read_parquet(path)
        result[family]=save_frame(engine,family,frame)
        print(f'PostgreSQL import: {family} {len(frame):,} rows',flush=True)
    feed_status(engine,'historical:parquet',dict(status='imported',rows=result,source=str(root)))
    return result


def candles(engine, market=PERP, timeframe='5m', limit=1000, before=None, include_forming=False):
    if market not in (SYMBOL,PERP) or timeframe not in TIMEFRAMES or not 1<=limit<=5000:
        raise ValueError('unsupported market, timeframe or limit')
    market=PERP
    end=utc(before) if before else pd.Timestamp.now(tz='UTC')
    # Bound reads in SQL; gaps do not expand this into unbounded full-history scans.
    with engine.connect() as connection:
        latest=connection.execute(select(func.max(MarketCandle.event_time)).where(
            MarketCandle.market==market,MarketCandle.interval=='5m',MarketCandle.event_time<end.to_pydatetime())).scalar()
        if latest is None:return []
        start=utc(latest)-pd.Timedelta(seconds=TIMEFRAMES[timeframe]*(limit+2))
        query=select(MarketCandle.__table__).where(MarketCandle.market==market,MarketCandle.interval=='5m',
            MarketCandle.event_time>=start.to_pydatetime(),
            MarketCandle.event_time<end.to_pydatetime()).order_by(MarketCandle.event_time)
        if not (include_forming and timeframe=='5m'):query=query.where(MarketCandle.complete.is_(True))
        rows=connection.execute(query).mappings().all()
    if not rows:return []
    frame=pd.DataFrame(rows).set_index('event_time')
    frame.index=pd.to_datetime(frame.index,utc=True)
    frequency={'5m':'5min','15m':'15min','30m':'30min','1h':'1h','4h':'4h','1d':'1D','1w':'7D'}[timeframe]
    grouped=frame.resample(frequency,origin=pd.Timestamp('1970-01-05',tz='UTC'),label='left',closed='left')
    agg=grouped.agg(dict(open='first',high='max',low='min',close='last',volume='sum',
        trade_count='sum',taker_buy_volume='sum',received_at='max',source='last',complete='all'))
    counts=grouped.close.count()
    agg['available_at']=agg.index+pd.Timedelta(seconds=TIMEFRAMES[timeframe])
    # Higher-timeframe indicators only see fully completed, gap-free candles.
    eligible=counts==TIMEFRAMES[timeframe]//300
    if not (include_forming and timeframe=='5m'):eligible&=agg.available_at<=end
    agg=agg[eligible].tail(limit)
    return [dict(event_time=t.isoformat(),**{k:(v.isoformat() if hasattr(v,'isoformat') else v) for k,v in row.items()})
            for t,row in agg.iterrows()]


def summary(engine=None):
    engine=engine or database()
    with engine.connect() as connection:
        bars=connection.execute(select(MarketCandle.market,func.count().label('rows'),
            func.min(MarketCandle.event_time).label('first'),func.max(MarketCandle.event_time).label('last'))
            .where(MarketCandle.market==PERP).group_by(MarketCandle.market)).mappings().all()
        families=connection.execute(select(MarketObservation.market,MarketObservation.family,func.count().label('rows'),
            func.min(MarketObservation.event_time).label('first'),func.max(MarketObservation.event_time).label('last'))
            .where(MarketObservation.market==PERP).group_by(MarketObservation.market,MarketObservation.family)).mappings().all()
        feeds=connection.execute(select(MarketFeed.__table__).where(~MarketFeed.id.like('archive:%'),
            MarketFeed.id!='history:reference')
            .order_by(MarketFeed.updated_at.desc()).limit(20)).mappings().all()
    active_feeds=[]
    for row in feeds:
        item=dict(row);state=dict(item['state'])
        for key in ('markets','rows'):
            if isinstance(state.get(key),dict) and PERP in state[key]:
                state[key]={SYMBOL:state[key][PERP]}
        item['state']=state;active_feeds.append(item)
    return dict(engine=engine.dialect.name,symbol=SYMBOL,market_type='USD-M perpetual',
                markets=[dict(row,market=SYMBOL) for row in bars],
                families=[dict(row,market=SYMBOL) for row in families],feeds=active_feeds)


def window_coverage(engine=None,as_of=None,years=None,persist=True):
    """Count actual native timestamps; first/last dates alone do not prove coverage."""
    from .history import history_window
    from .data import timestamp_ranges
    engine=engine or database(settings.market_database_url)
    start,end=history_window(as_of,years)
    expected=pd.date_range(start,end,freq='5min',inclusive='left')
    markets={}
    with engine.connect() as connection:
        for market in (PERP,):
            observed=connection.execute(select(MarketCandle.event_time).where(
                MarketCandle.market==market,MarketCandle.interval=='5m',MarketCandle.complete.is_(True),
                MarketCandle.event_time>=start.to_pydatetime(),MarketCandle.event_time<end.to_pydatetime())
                .order_by(MarketCandle.event_time)).scalars().all()
            index=pd.to_datetime(observed,utc=True)
            missing=expected.difference(index)
            markets[SYMBOL]=dict(expected=len(expected),stored=len(expected.intersection(index)),missing=len(missing),
                complete=not len(missing),gap_ranges=timestamp_ranges(missing)[:20])
    result=dict(start=start.isoformat(),end_exclusive=end.isoformat(),years=years or settings.market_history_years,
                markets=markets,scope='Completed five-minute price candles; external families have separate source coverage.')
    if persist:feed_status(engine,'history:five_year_coverage',result)
    return result
