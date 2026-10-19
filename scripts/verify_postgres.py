"""Real PostgreSQL COPY/upsert, aggregation and live-ingestion verification in a disposable DB."""
import json
from pathlib import Path
import uuid
import httpx
import pandas as pd
from sqlalchemy import create_engine, text, select, func
from quantpilot.config import settings
from quantpilot.store import database, initialize
from quantpilot.market_store import MarketCandle, MarketObservation, save_frame, candles, summary
from quantpilot.collector import collect_once
from quantpilot.operations import begin_run,finish_run,report
from quantpilot.store import portfolio,apply_events
from alembic import command
from alembic.config import Config


def main():
    Path('.verification').mkdir(exist_ok=True)
    owner=database(settings.market_database_url)
    assert owner.dialect.name=='postgresql'
    name='quantpilot_verify_'+uuid.uuid4().hex[:12]
    admin=create_engine(owner.url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine=create_engine(owner.url.set(database=name))
    try:
        original_url=settings.database_url
        isolated_url=engine.url.render_as_string(hide_password=False)
        try:
            settings.database_url=isolated_url
            command.upgrade(Config('alembic.ini'),'head')
        finally:
            database(isolated_url).dispose()
            settings.database_url=original_url
        initialize(engine)
        now=pd.Timestamp.now(tz='UTC').floor('5min')
        def response(request):
            raw=[]
            for stamp in [now-pd.Timedelta(minutes=5),now]:
                raw.append([int(stamp.timestamp()*1000),'100','102','99','101','10',
                    int((stamp+pd.Timedelta(minutes=5)).timestamp()*1000)-1,'1010',20,'6','606','0'])
            # Some archives/provider batches repeat timestamps within one batch.
            return httpx.Response(200,json=raw+raw)
        with httpx.Client(transport=httpx.MockTransport(response)) as client:
            collect_once(engine,client);collect_once(engine,client)
        with engine.connect() as connection:
            assert connection.execute(select(func.count()).select_from(MarketCandle)).scalar()==2
            assert connection.execute(select(func.count()).select_from(MarketCandle).where(MarketCandle.complete.is_(False))).scalar()==1
        frame=pd.DataFrame([dict(event_time=now,available_at=now,received_at=now,source='fixture:postgres-integration',
            open_interest=1,global_ratio=1,top_account_ratio=float('nan'),top_position_ratio=1)])
        save_frame(engine,'positioning',frame);save_frame(engine,'positioning',frame)
        with engine.connect() as connection:
            row=connection.execute(select(MarketObservation.__table__)).mappings().one()
            assert row['values']['top_account_ratio'] is None and not row['valid']
        result=candles(engine,limit=10)
        assert len(result)==1 and result[0]['close']==101
        state=portfolio(engine);run=begin_run(engine,state)
        stamp=pd.Timestamp.now(tz='UTC').isoformat()
        state=apply_events(engine,[dict(id='fixture:signal',kind='signal',time=stamp,forecast=dict(
            probability=.5,up_magnitude=.001,down_magnitude=.001,atr=1,observed_at=stamp,
            model_version='fixture-model',funding_observable=True,funding_rate=0)),
            dict(id='fixture:checkpoint',kind='checkpoint',time=stamp)])
        finish_run(engine,run,'succeeded','complete',.1,state,model_version='fixture-model')
        operational=report(engine,model_version='fixture-model',coverage_ready=True)
        assert operational['ready'] and operational['forward_evidence']['forward_decisions']==1
        assert operational['worker']['run_totals']=={'succeeded':1}
        failed=begin_run(engine,state)
        finish_run(engine,failed,'failed','snapshots',.2,state,error_type='FixtureTimeout')
        assert report(engine)['worker']['status']=='failed'
        live_summary=summary(owner)
        output=dict(status='passed',database='PostgreSQL',disposable_database=name,
            checks=['COPY upsert idempotency','forming/complete separation','JSON NULL quality flag',
                    'completed candle query','continuous ingestion persistence',
                    'fresh Alembic migration through worker-run schema','persistent worker outcomes and readiness'],
            live_database_counts=live_summary)
        Path('.verification/postgres-platform.json').write_text(json.dumps(output,indent=2,default=str),encoding='utf-8')
        print('Real PostgreSQL integration: 7 checks passed; isolated test database removed on exit.')
    finally:
        engine.dispose()
        with admin.connect() as connection:connection.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


if __name__=='__main__':main()
