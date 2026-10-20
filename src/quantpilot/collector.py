"""Continuous server-side persistence, independent of browser WebSocket charts."""
import time
import httpx
import pandas as pd
from .config import settings
from .data import utc
from .store import database, initialize
from .market_store import PERP, MarketCandle, _upsert, feed_status
from sqlalchemy import select,func


def collect_once(engine=None, client=None):
    engine=engine or database(settings.market_database_url)
    if client is None:
        with httpx.Client(timeout=15) as owned:return collect_once(engine,owned)
    now=pd.Timestamp.now(tz='UTC')
    def get(base,path,params):
        response=client.get(base+path,params=params);response.raise_for_status();return response.json()
    counts={}
    for market,base in [(PERP,'https://fapi.binance.com')]:
        path='/fapi/v1/klines'
        with engine.connect() as connection:
            last=connection.execute(select(func.max(MarketCandle.event_time)).where(
                MarketCandle.market==market,MarketCandle.complete.is_(True))).scalar()
        since=utc(last) if last else now-pd.Timedelta(days=2)
        raw=[]
        while since<now:
            batch=get(base,path,dict(symbol='BTCUSDT',interval='5m',startTime=int(since.timestamp()*1000),limit=1000))
            if not batch:break
            raw.extend(batch)
            cursor=pd.to_datetime(batch[-1][0],unit='ms',utc=True)+pd.Timedelta(minutes=5)
            if cursor<=since:raise ValueError('live pagination did not advance')
            since=cursor
        if not raw:raise ValueError('Provider returned no candles; retaining last persisted observation')
        received=pd.Timestamp.now(tz='UTC')
        rows=[]
        for bar in raw:
            import math
            o,h,l,c,v=map(float,bar[1:6])
            if not all(math.isfinite(value) for value in (o,h,l,c,v)) or min(o,h,l,c)<=0 or v<0 or h<max(o,c,l) or l>min(o,c):
                raise ValueError('invalid provider OHLC')
            event=pd.to_datetime(bar[0],unit='ms',utc=True)
            rows.append(dict(market=market,interval='5m',event_time=event.to_pydatetime(),
                available_at=(event+pd.Timedelta(minutes=5)).to_pydatetime(),received_at=received.to_pydatetime(),
                source=base+path,open=float(bar[1]),high=float(bar[2]),low=float(bar[3]),close=float(bar[4]),
                volume=float(bar[5]),trade_count=float(bar[8]),taker_buy_volume=float(bar[9]),
                complete=event+pd.Timedelta(minutes=5)<=received))
        table=MarketCandle.__table__
        counts[market]=_upsert(engine,table,rows,['market','interval','event_time'],
            lambda stmt:(stmt.excluded.received_at>=table.c.received_at)&((~table.c.complete)|stmt.excluded.complete))
    persisted=pd.Timestamp.now(tz='UTC')
    feed_status(engine,'live:collector',dict(status='connected',updated_at=persisted.isoformat(),rows=counts,
        transport='REST',poll_interval_seconds=settings.market_poll_seconds,last_persisted_at=persisted.isoformat(),
        note='Completed and forming candles persisted; forming candles excluded from research.'))
    return counts


def run(interval=None,once=False):
    interval=settings.market_poll_seconds if interval is None else interval
    if not 1<=interval<=60:raise ValueError('collector interval must be between 1 and 60 seconds')
    engine=database(settings.market_database_url);initialize(engine)
    failures=0
    with httpx.Client(timeout=15) as client:
        while True:
            try:
                collect_once(engine,client)
                failures=0
            except Exception as exc:
                failures+=1
                feed_status(engine,'live:collector',dict(status='disconnected',error=str(exc),
                    consecutive_failures=failures,next_retry_seconds=min(120,interval*2**min(failures,4))))
                if once:raise
            if once:return
            time.sleep(min(120,interval*2**min(failures,4)))
