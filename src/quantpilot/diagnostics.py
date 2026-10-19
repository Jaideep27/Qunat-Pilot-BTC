"""Read-only local readiness across storage, application code and running services."""
from datetime import datetime,timezone
import shutil
import httpx
from sqlalchemy import text
from . import services
from .config import settings
from .store import database
from .market_store import window_coverage


def doctor(client=None):
    checks={};details={}
    try:
        result=services.operations(3)['data']
        checks['current_code_ready']={'ok':result['ready'],'status':'ready' if result['ready'] else 'needs_attention'}
        details['operations']=result
        coverage=window_coverage(database(settings.market_database_url),persist=False)
        complete=bool(coverage['markets']) and all(item['complete'] for item in coverage['markets'].values())
        checks['five_year_candles']={'ok':complete,'status':'complete' if complete else 'gaps'}
        details['candle_coverage']=coverage
        with database(settings.market_database_url).connect() as connection:
            details['market_database_bytes']=connection.execute(text('SELECT pg_database_size(current_database())')).scalar_one() if connection.dialect.name=='postgresql' else None
        details['free_disk_bytes']=shutil.disk_usage(settings.data_dir.resolve()).free
    except Exception as exc:
        checks['current_code_ready']={'ok':False,'status':'unavailable','error_type':type(exc).__name__}
    own=client is None
    client=client or httpx.Client(timeout=10)
    try:
        for name,url in [('running_api','http://127.0.0.1:8000/api/v1/health'),
            ('running_monitor','http://127.0.0.1:8000/api/v1/operations?limit=1'),
            ('dashboard','http://127.0.0.1:5173/')]:
            try:
                response=client.get(url)
                ok=response.status_code==200
                if name=='running_monitor' and ok:ok=isinstance(response.json().get('data',{}).get('ready'),bool)
                checks[name]={'ok':ok,'status':'reachable' if ok else ('reload_required' if name=='running_monitor' and response.status_code==404 else 'unavailable'),
                    'http_status':response.status_code}
            except Exception as exc:checks[name]={'ok':False,'status':'unavailable','error_type':type(exc).__name__}
    finally:
        if own:client.close()
    ready=all(check['ok'] for check in checks.values())
    return dict(ready=ready,
        checked_at=datetime.now(timezone.utc).isoformat(),checks=checks,details=details,
        next_action=None if ready else ('node scripts/start.cjs --restart-services' if checks['running_monitor']['status']=='reload_required' else 'Inspect failed readiness gates'),
        scope='Read-only local checks; no provider requests, worker execution, fills or service restarts')
