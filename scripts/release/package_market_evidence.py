"""Package the actual PostgreSQL/chart milestone status without claiming blocked downloads succeeded."""
from pathlib import Path
import json
import hashlib
import re
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
from quantpilot.storage.market import summary


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def log(path):
    raw=Path(path).read_bytes()
    return raw.decode('utf-16' if raw.startswith(b'\xff\xfe') else 'utf-8-sig')


suite=ET.parse('.verification/pytest-platform.xml').getroot().find('testsuite')
assert suite is not None and suite.attrib['failures']==suite.attrib['errors']=='0'
browser=re.search(r'(\d+) passed \(([\d.]+)s\)',log('.verification/workspace-browser-final.log'))
assert browser and int(browser[1])==6
assert 'built in' in log('.verification/workspace-build-final.log')
pg=json.loads(Path('.verification/postgres-platform.json').read_text(encoding='utf-8'))
assert pg['status']=='passed'
smoke=json.loads(Path('.verification/release-smoke.json').read_text(encoding='utf-8'))
assert smoke['status']=='passed' and smoke['mcp_tools_matched']==9
storage=summary()
files=[*Path('src/quantpilot').rglob('*.py'),*Path('frontend/src').rglob('*.*'),
       *Path('tests').rglob('*.py'),Path('frontend/tests/workspace.spec.ts'),
       Path('scripts/start.cjs'),Path('scripts/ops/serve_api.py'),Path('scripts/ops/data_supervisor.py'),
       Path('scripts/ops/configure_postgres.py'),Path('scripts/verify/verify_postgres.py'),
       Path('migrations/versions/0003_market_history.py'),Path('compose.yaml')]
shot=Path('frontend/test-results/postgres-workspace.png')
assert shot.exists()
target=Path('docs/screenshots/postgres-workspace.png');shutil.copy2(shot,target)
result=dict(verified_at=datetime.now(timezone.utc).isoformat(),status='local_database_and_chart_verified_provider_sync_blocked',
    database=storage,stored_rows=sum(m['rows'] for m in storage['markets'])+sum(f['rows'] for f in storage['families']),
    earliest_requested_history_complete=False,real_live_provider_verified=False,
    external_blocker='WinError 10013: outbound provider sockets denied in this session',
    resume_command='node scripts/start.cjs (from the regular network-enabled terminal)',
    python=dict(passed=int(suite.attrib['tests']),failures=0,errors=0,seconds=float(suite.attrib['time'])),
    browser=dict(passed=int(browser[1]),seconds=float(browser[2]),engine='Playwright with installed Chrome'),
    postgres_integration=pg['checks'],mcp=dict(tools_matched=9,shared_api_data_parity=True),
    chart=dict(native_studies=86,timeframes=['5m','15m','30m','1h','4h','1d','1w'],
        styles=['Candles','Bars','Line','Area'],drawings=['trend','ray','horizontal','vertical','rectangle','fibonacci','measure','text'],
        drawing_storage='Browser local storage, per market/timeframe',
        tradingview_widget='Official hosted widget supplied; external availability not verified in this session'),
    screenshot=dict(path=target.as_posix(),sha256=digest(target)),
    source_sha256={p.as_posix():digest(p) for p in files},
    evidence_sha256={p:digest(p) for p in ['.verification/pytest-platform.xml','.verification/workspace-browser-final.log',
        '.verification/postgres-platform.json','.verification/workspace-build-final.log','.verification/release-smoke.json']})
Path('docs/evidence/market-platform-verification.json').write_text(json.dumps(result,indent=2,default=str)+'\n',encoding='utf-8')
Path('docs/evidence/latest-verification.json').write_text(json.dumps(dict(status=result['status'],verified_at=result['verified_at'],
    frozen_model_version='24c8238525d4',python_passed=int(suite.attrib['tests']),browser_passed=int(browser[1]),
    mcp_tools_matched=9,postgresql_stored_rows=result['stored_rows'],
    evidence='docs/evidence/market-platform-verification.json',research_evidence='docs/evidence/release-verification.json',
    complete_earlier_history='blocked by provider socket permissions',real_live_feed='blocked by provider socket permissions'),indent=2)+'\n',encoding='utf-8')
print(json.dumps({key:result[key] for key in ['status','stored_rows','python','browser','real_live_provider_verified']},indent=2))
