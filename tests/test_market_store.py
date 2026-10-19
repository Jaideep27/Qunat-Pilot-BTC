import pandas as pd
import pytest
from sqlalchemy import select,func
from quantpilot.market_store import save_frame,candles,summary,MarketCandle,MarketObservation,PERP,SPOT,SYMBOL
from quantpilot.charting import studies


def test_market_import_is_idempotent_source_separated_and_rejects_old_retries(account,candles):
    frame=candles.iloc[:24].copy()
    save_frame(account,'candles',frame)
    save_frame(account,'candles',frame)
    save_frame(account,'candles',frame,SPOT)
    older=frame.copy();older['received_at']-=pd.Timedelta(days=1);older[['open','high','low','close']]*=2
    save_frame(account,'candles',older)
    active=summary(account)
    assert active['markets'][0]['market']==SYMBOL and len(active['markets'])==1
    assert active['markets'][0]['rows']==24
    with pytest.raises(ValueError,match='unsupported market'):
        globals()['candles'](account,SPOT)
    with account.connect() as connection:
        assert connection.execute(select(func.count()).select_from(MarketCandle)).scalar()==48
    result=globals()['candles'](account,PERP,'1h',10,before='2024-01-01T02:00:00Z')
    assert len(result)==2
    assert result[0]['open']==frame.open.iloc[0]
    assert result[0]['high']==frame.high.iloc[:12].max()
    assert result[0]['close']==frame.close.iloc[11]
    assert result[0]['volume']==pytest.approx(frame.volume.iloc[:12].sum())


def test_higher_timeframe_rejects_partial_and_gapped_bars(account,candles):
    frame=candles.iloc[:24].drop(index=5)
    save_frame(account,'candles',frame)
    result=globals()['candles'](account,PERP,'1h',10,before='2024-01-01T01:59:00Z')
    assert result==[]


def test_invalid_external_values_are_stored_with_quality_flag(account,frames):
    position=frames['positioning'].iloc[:2].copy()
    position.loc[0,'top_account_ratio']=float('nan')
    save_frame(account,'positioning',position)
    with account.connect() as connection:
        row=connection.execute(select(MarketObservation.__table__).order_by(MarketObservation.event_time)).mappings().first()
    assert row['valid'] is False and row['values']['top_account_ratio'] is None


def test_chart_studies_future_changes_do_not_rewrite_earlier_points(candles):
    rows=candles.iloc[:400].to_dict('records')
    original=studies(rows)
    changed=candles.iloc[:400].copy()
    changed.loc[300:,['open','high','low','close']]*=2
    actual=studies(changed.to_dict('records'))
    boundary=int(candles.event_time.iloc[300].timestamp())
    assert len(original['catalogue'])>=80
    for name,points in original['series'].items():
        assert [p for p in points if p['time']<boundary]==[p for p in actual['series'][name] if p['time']<boundary]


def test_market_api_has_bounded_inputs_and_uses_persistent_store(account,candles):
    from fastapi.testclient import TestClient
    from quantpilot.api import app
    save_frame(account,'candles',candles.iloc[:300])
    save_frame(account,'candles',candles.iloc[:300],SPOT)
    with TestClient(app) as client:
        parameters=client.get('/openapi.json').json()['paths']['/api/v1/market/candles']['get']['parameters']
        market_schema=next(item['schema'] for item in parameters if item['name']=='market')
        assert market_schema.get('const')==SYMBOL
        storage=client.get('/api/v1/market/storage').json()['data']
        assert storage['engine']=='sqlite' and [row['market'] for row in storage['markets']]==[SYMBOL]
        assert len(client.get('/api/v1/market/candles?market=BTCUSDT&limit=10').json()['data'])==10
        assert client.get('/api/v1/market/candles?market=BTCUSDT_SPOT').status_code==422
        assert client.get('/api/v1/market/studies?market=BTCUSDT_SPOT').status_code==422
        assert client.get('/api/v1/market/reference').status_code==404
        assert client.get('/api/v1/market/candles?timeframe=1s').status_code==422
        assert client.get('/api/v1/market/candles?limit=5001').status_code==422
        assert client.get('/api/v1/market/studies?limit=400').json()['data']['catalogue']


def test_coverage_counts_internal_gaps_instead_of_only_first_and_last(account,candles):
    from quantpilot.market_store import window_coverage
    save_frame(account,'candles',candles.iloc[:288].drop(index=10))
    report=window_coverage(account,as_of='2024-01-02T00:00:00Z',years=1)
    assert set(report['markets'])=={SYMBOL}
    result=report['markets'][SYMBOL]
    assert result['stored']==287
    assert result['missing']==result['expected']-287
    assert result['complete'] is False
    assert len(result['gap_ranges'])==2


def test_duplicate_source_batches_deduplicate_versions_and_report_conflicts(account,candles):
    frame=candles.iloc[:1].copy()
    assert save_frame(account,'candles',pd.concat([frame,frame]))==1
    newer=frame.copy();newer['received_at']+=pd.Timedelta(seconds=1)
    newer[['open','high','low','close']]*=2
    assert save_frame(account,'candles',pd.concat([newer,frame]))==1
    stored=globals()['candles'](account,SYMBOL,limit=1)
    assert stored[0]['close']==newer.close.iloc[0]
    conflict=newer.copy();conflict[['open','high','low','close']]*=2
    with pytest.raises(ValueError,match='conflicting duplicate'):
        save_frame(account,'candles',pd.concat([newer,conflict]))
