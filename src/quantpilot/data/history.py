"""Resumable, source-separated history acquisition into the market database."""
import hashlib
import io
import time
import zipfile
import httpx
import pandas as pd
from sqlalchemy import select
from quantpilot.config import settings
from quantpilot.data.archives import BASE, fetch, parse, utc, KLINES
from quantpilot.storage.market import PERP, SPOT, REFERENCE, MarketFeed, feed_status, save_frame
from quantpilot.storage.database import database, initialize


def history_window(until=None,years=None):
    end=utc(until or pd.Timestamp.now(tz='UTC')).floor('5min')
    years=settings.market_history_years if years is None else years
    return end-pd.DateOffset(years=years),end


def archive_items(until=None,years=None,all_history=False):
    start_target,end_target=history_window(until,years)
    end=end_target.normalize().replace(day=1)
    # Fetch the boundary month, then filter its rows to the exact requested window.
    for market,start in ((PERP,'2019-09-01'),):
        first=utc(start) if all_history else max(utc(start),start_target.normalize().replace(day=1))
        for month in pd.date_range(first,end,freq='MS',inclusive='left',tz='UTC'):
            families=[
                ('candles','klines'),('mark','markPriceKlines'),('index','indexPriceKlines'),('funding','fundingRate')]
            for family,directory in families:
                stem=f'BTCUSDT-fundingRate-{month:%Y-%m}' if family=='funding' else f'BTCUSDT-5m-{month:%Y-%m}'
                base=BASE
                part=f'{directory}/BTCUSDT/'+('' if family=='funding' else '5m/')+stem+'.zip'
                yield dict(market=market,family=family,url=base+'/monthly/'+part,
                           filename=f'{market}-{family}-{stem}.zip')
    first_day=utc('2019-09-01') if all_history else max(utc('2019-09-01'),start_target.normalize())
    for day in pd.date_range(first_day,end,freq='D',inclusive='left',tz='UTC'):
        stem=f'BTCUSDT-metrics-{day:%Y-%m-%d}.zip'
        yield dict(market=PERP,family='positioning',url=BASE+'/daily/metrics/BTCUSDT/'+stem,
                   filename=PERP+'-'+stem)


def _parse(record,root):
    if record['market']!=SPOT:return parse(record,root)
    with zipfile.ZipFile(root/'raw'/record['filename']) as archive:
        names=[name for name in archive.namelist() if name.endswith('.csv')]
        if len(names)!=1:raise ValueError('expected one spot CSV')
        raw=archive.read(names[0])
    frame=pd.read_csv(io.BytesIO(raw),header=None,names=KLINES)
    frame=frame[pd.to_numeric(frame.open_time,errors='coerce').notna()].copy()
    stamps=pd.to_numeric(frame.open_time)
    # Binance spot archive switched to microseconds in 2025.
    unit='us' if stamps.median()>10**14 else 'ms'
    frame['event_time']=pd.to_datetime(stamps,unit=unit,utc=True)
    frame['available_at']=frame.event_time+pd.Timedelta(minutes=5)
    frame['received_at']=utc(record['received_at'])
    frame['source']=record['url']
    values=['open','high','low','close','volume','trade_count','taker_buy_volume']
    for column in values:frame[column]=pd.to_numeric(frame[column],errors='raise')
    return frame[['event_time','available_at','received_at','source',*values]]


def backfill(until=None,years=None,all_history=False):
    engine=database(settings.market_database_url)
    initialize(engine)
    root=settings.data_dir/'market-history'
    (root/'raw').mkdir(parents=True,exist_ok=True)
    start,end=history_window(until,years)
    feed_status(engine,'history:target',dict(years=years or settings.market_history_years,
        start=start.isoformat(),end=end.isoformat(),mode='all_available' if all_history else 'last_five_years',
        note='Existing older records are retained; the frozen research dataset is unchanged.'))
    with httpx.Client(timeout=30,follow_redirects=True) as client:
        for item in archive_items(until,years,all_history):
            key='archive:'+hashlib.sha256(item['url'].encode()).hexdigest()[:32]
            with engine.connect() as connection:
                done=connection.execute(select(MarketFeed.state).where(MarketFeed.id==key)).scalar()
            if done and done.get('status')=='imported':
                previous=done.get('filtered_window_start')
                if previous is None or (not all_history and start>=utc(previous)):continue
            record=fetch(item,root,client)
            if record['status']!='verified':
                feed_status(engine,key,dict(status='unavailable',url=item['url'],error=record.get('error')))
                if '10013' in record.get('error',''):
                    raise RuntimeError('Provider sockets denied; history job checkpointed and will resume')
                continue
            frame=_parse(record,root)
            if not all_history:frame=frame[(frame.event_time>=start)&(frame.event_time<end)]
            rows=save_frame(engine,item['family'],frame,item['market'])
            feed_status(engine,key,dict(status='imported',rows=rows,url=item['url'],sha256=record['sha256'],
                filtered_window_start=None if all_history else start.isoformat()))
            print(f"{item['market']} {item['family']}: {rows:,} rows",flush=True)
            time.sleep(.15)
    feed_status(engine,'history:overall',dict(status='archive_pass_finished',
        note='Unavailable archives remain listed individually; history is not claimed complete.'))


def backfill_reference(client,engine,root):
    url='https://raw.githubusercontent.com/coinmetrics/data/master/csv/btc.csv'
    response=client.get(url);response.raise_for_status()
    path=root/'raw'/'coinmetrics-btc.csv';path.write_bytes(response.content)
    frame=pd.read_csv(io.BytesIO(response.content),usecols=['time','PriceUSD'])
    frame['PriceUSD']=pd.to_numeric(frame.PriceUSD,errors='coerce')
    frame=frame[frame.PriceUSD.notna()&(frame.PriceUSD>0)].copy()
    frame['event_time']=pd.to_datetime(frame.time,utc=True)
    frame['available_at']=frame.event_time+pd.Timedelta(days=1)
    frame['received_at']=pd.Timestamp.now(tz='UTC');frame['source']=url
    frame=frame.rename(columns={'PriceUSD':'price_usd'})
    count=save_frame(engine,'reference',frame,REFERENCE)
    feed_status(engine,'history:reference',dict(status='imported',rows=count,source=url,
        sha256=hashlib.sha256(response.content).hexdigest(),license='CC BY-NC 4.0; attribution required',
        note='Daily reference prices only. No OHLC or volume invented. Pre-price blockchain days have no price.'))
