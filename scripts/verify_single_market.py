"""Inspect the running market mode without changing or stopping any service."""
import json
from pathlib import Path
import httpx
import pandas as pd
from sqlalchemy import select,func
from quantpilot.config import settings
from quantpilot.storage.market import PERP,SYMBOL,MarketCandle,MarketFeed,summary,window_coverage
from quantpilot.storage.database import database


def main():
    engine=database(settings.market_database_url)
    storage=summary(engine)
    coverage=window_coverage(engine)
    now=pd.Timestamp.now(tz='UTC')
    with engine.connect() as connection:
        feed=connection.execute(select(MarketFeed.__table__).where(MarketFeed.id=='live:collector')).mappings().first()
        inactive=connection.execute(select(MarketCandle.market,func.count().label('rows'))
            .where(MarketCandle.market!=PERP).group_by(MarketCandle.market)).mappings().all()
    collector_rows=feed['state'].get('rows',{}) if feed else {}
    collector_current=bool(feed and feed['state'].get('status')=='connected'
        and (now-pd.to_datetime(feed['updated_at'],utc=True)).total_seconds()<120)
    single_collector=bool(collector_rows) and set(collector_rows)<={PERP,SYMBOL}
    api_markets=[];api_error=None
    try:
        response=httpx.get('http://127.0.0.1:8000/openapi.json',timeout=10);response.raise_for_status()
        parameters=response.json()['paths']['/api/v1/market/candles']['get']['parameters']
        schema=next(item['schema'] for item in parameters if item['name']=='market')
        api_markets=schema.get('enum',[schema['const']] if 'const' in schema else [])
    except Exception as exc:api_error=str(exc)
    active_only=[item['market'] for item in storage['markets']]==[SYMBOL]
    api_current=api_markets==[SYMBOL]
    ready=active_only and api_current and single_collector and collector_current
    result=dict(verified_at=now.isoformat(),status='active' if ready else 'restart_or_connection_check_required',
        symbol=SYMBOL,source_market='Binance USD-M perpetual',storage=storage['markets'],
        five_year_coverage=coverage,collector_connected=collector_current,
        collector_single_market=single_collector,api_market_values=api_markets,api_error=api_error,
        preserved_inactive_candles=[dict(row) for row in inactive],
        restart_command='node scripts/start.cjs --restart-services' if not (api_current and single_collector) else None,
        note='Inactive archives are preserved and never merged into BTCUSDT. Coverage applies to price candles only.')
    path=Path('docs/evidence/single-market-status.json')
    path.write_text(json.dumps(result,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2,default=str))
    if not ready:raise SystemExit(2)


if __name__=='__main__':main()
