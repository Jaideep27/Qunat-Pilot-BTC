"""Read actual local state through the current shared services; never run a worker."""
import json
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from quantpilot.api import app


def main():
    # No lifespan context: this check only reads the existing schema/account.
    client=TestClient(app)
    response=client.get('/api/v1/operations?limit=10')
    response.raise_for_status()
    result=response.json()['data']
    ready=client.get('/api/v1/ready')
    assert ready.status_code==(200 if ready.json()['data']['ready'] else 503)
    assert client.get('/api/v1/operations?limit=101').status_code==422
    assert client.post('/api/v1/operations').status_code==405
    result['verification']={
        'mode':'Actual PostgreSQL state; current code inspected in-process without worker execution',
        'readiness_http_status':ready.status_code,
        'checks':['Current-code operations response','Readiness HTTP semantics','Bounded input','Read-only endpoint'],
        'running_api_operations_status':None,
        'running_api_loaded_monitoring':False,
        'running_api_error_type':None}
    try:
        running=httpx.get('http://127.0.0.1:8000/api/v1/operations?limit=1',timeout=10)
        result['verification']['running_api_operations_status']=running.status_code
        result['verification']['running_api_loaded_monitoring']=running.status_code==200
    except httpx.HTTPError as exc:
        result['verification']['running_api_error_type']=type(exc).__name__
    output=Path('docs/evidence/operations-runtime.json')
    output.write_text(json.dumps(result,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('checked_at','ready','checks','collector','forward_evidence','verification')},indent=2))


if __name__=='__main__':main()
