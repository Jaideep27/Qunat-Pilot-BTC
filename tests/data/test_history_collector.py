import zipfile
import httpx
import pandas as pd
import pytest
from sqlalchemy import select,func
from quantpilot.data.history import archive_items,_parse
from quantpilot.data.collector import collect_once
from quantpilot.storage.market import MarketCandle,MarketFeed,PERP,SPOT,save_frame


def test_history_requests_only_btcusdt_perpetual_and_uses_utc():
    items=list(archive_items('2020-02-14',all_history=True))
    assert '/2019-' not in items[0]['url']  # dates are in filenames
    assert items[0]['market']==PERP and items[0]['url'].endswith('BTCUSDT-5m-2019-09.zip')
    assert {item['market'] for item in items}=={PERP}
    assert all('/futures/um/' in item['url'] for item in items)
    assert not any('2020-02.zip' in item['url'] for item in items)


def test_default_history_window_requests_only_latest_five_years():
    from quantpilot.data.history import history_window
    start,end=history_window('2026-10-19T09:12:00Z')
    assert start==pd.Timestamp('2021-10-19T09:10:00Z')
    assert end==pd.Timestamp('2026-10-19T09:10:00Z')
    items=list(archive_items('2026-10-19T09:12:00Z'))
    assert items[0]['url'].endswith('BTCUSDT-5m-2021-10.zip')
    assert not any('2020-' in item['url'] for item in items)
    assert {item['market'] for item in items}=={PERP}


@pytest.mark.parametrize('unit',['ms','us'])
def test_spot_archive_timestamp_units_are_normalized(tmp_path,unit):
    (tmp_path/'raw').mkdir()
    start=pd.Timestamp('2025-01-01',tz='UTC')
    multiplier=1000 if unit=='ms' else 1000000
    t=int(start.timestamp()*multiplier)
    raw=f'{t},100,102,99,101,10,{t+300*multiplier-1},1010,20,6,606,0\n'
    with zipfile.ZipFile(tmp_path/'raw'/'spot.zip','w') as z:z.writestr('spot.csv',raw)
    frame=_parse(dict(market=SPOT,filename='spot.zip',url='fixture:spot',received_at=start.isoformat()),tmp_path)
    assert frame.event_time.iloc[0]==start and frame.available_at.iloc[0]==start+pd.Timedelta(minutes=5)


def test_collector_persists_closed_and_forming_candles_and_rejects_bad_ohlc(account):
    now=pd.Timestamp.now(tz='UTC').floor('5min')
    raw=[[int(t.timestamp()*1000),100,102,99,101,10,0,1010,20,6,606,0] for t in [now-pd.Timedelta(minutes=5),now]]
    requests=[]
    def response(request):
        requests.append(request)
        return httpx.Response(200,json=raw)
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        assert set(collect_once(account,client))=={PERP}
        collect_once(account,client)
    assert all(request.url.host=='fapi.binance.com' and request.url.params['symbol']=='BTCUSDT' for request in requests)
    with account.connect() as connection:
        assert connection.execute(select(func.count()).select_from(MarketCandle)).scalar()==2
        assert connection.execute(select(func.count()).select_from(MarketCandle).where(MarketCandle.complete.is_(False))).scalar()==1
    raw[0][2]=50
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=raw))) as client:
        with pytest.raises(ValueError,match='OHLC'):collect_once(account,client)


def test_collector_recovers_paginated_downtime_without_duplicate_or_missing_bars(account):
    now=pd.Timestamp.now(tz='UTC').floor('5min')
    events=pd.date_range(end=now,periods=1002,freq='5min')
    seed=pd.DataFrame([dict(event_time=events[0],available_at=events[0]+pd.Timedelta(minutes=5),
        received_at=events[0],source='fixture:before-downtime',open=100,high=102,low=99,close=101,volume=10)])
    save_frame(account,'candles',seed)
    raw=[[int(t.timestamp()*1000),100,102,99,101,10,0,1010,20,6,606,0] for t in events]
    calls=[]
    def response(request):
        calls.append(request)
        since=int(request.url.params['startTime'])
        return httpx.Response(200,json=[bar for bar in raw if bar[0]>=since][:1000])
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        collect_once(account,client)
        assert len(calls)==2
        collect_once(account,client)
    with account.connect() as connection:
        observed=connection.execute(select(MarketCandle.event_time).order_by(MarketCandle.event_time)).scalars().all()
        assert list(pd.to_datetime(observed,utc=True))==list(events)
        assert connection.execute(select(func.count()).select_from(MarketCandle).where(MarketCandle.complete.is_(False))).scalar()==1


def test_persistence_loop_reuses_connection_and_saves_forming_updates_every_five_seconds(account,monkeypatch):
    from quantpilot.data import collector
    from quantpilot.config import settings
    now=pd.Timestamp.now(tz='UTC').floor('5min')
    requests=[];clients=[];sleeps=[]
    original=httpx.Client
    def response(request):
        requests.append(request)
        return httpx.Response(200,json=[[int(now.timestamp()*1000),100,103,99,100+len(requests),10,0,1010,20,6,606,0]])
    def client_factory(**kwargs):
        client=original(transport=httpx.MockTransport(response),**kwargs);clients.append(client);return client
    class Finished(Exception):pass
    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps)==2:raise Finished
    monkeypatch.setattr(collector.httpx,'Client',client_factory)
    monkeypatch.setattr(collector.time,'sleep',sleep)
    monkeypatch.setattr(settings,'market_poll_seconds',5)
    with pytest.raises(Finished):collector.run()
    assert len(clients)==1 and clients[0].is_closed and len(requests)==2
    assert sleeps==[5,5]
    with account.connect() as connection:
        row=connection.execute(select(MarketCandle.__table__)).mappings().one()
        assert row['close']==102 and not row['complete']
        feed=connection.execute(select(MarketFeed.state).where(MarketFeed.id=='live:collector')).scalar_one()
        assert feed['poll_interval_seconds']==5 and feed['last_persisted_at']


def test_empty_provider_response_does_not_claim_success_or_erase_history(account,candles,monkeypatch):
    from quantpilot.data import collector
    save_frame(account,'candles',candles.iloc[:1])
    original=httpx.Client
    monkeypatch.setattr(collector.httpx,'Client',lambda **kwargs:original(
        transport=httpx.MockTransport(lambda request:httpx.Response(200,json=[])),**kwargs))
    with pytest.raises(ValueError,match='no candles'):collector.run(once=True)
    with account.connect() as connection:
        assert connection.execute(select(func.count()).select_from(MarketCandle)).scalar()==1
        feed=connection.execute(select(MarketFeed.state).where(MarketFeed.id=='live:collector')).scalar_one()
        assert feed['status']=='disconnected'
