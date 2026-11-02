"""Warm trusted native model libraries before starting Windows' async HTTP loop."""
from quantpilot.api import services
from quantpilot.monitoring.process import process_started
import uvicorn
import json
import os
import socket

# A second start must not overwrite the running API's record: bind would fail, the record would name a dead
# process, and every later restart would stop nothing while the old API kept serving old code (2026-11-02).
with socket.socket() as probe:
    probe.settimeout(2)
    if probe.connect_ex(('127.0.0.1',8000))==0:
        raise SystemExit('Port 8000 is already served by a running API; its runtime record is left unchanged.')
services.settings.data_dir.mkdir(exist_ok=True,parents=True)
(services.settings.data_dir/'api-runtime.json').write_text(json.dumps(dict(pid=os.getpid(),started=process_started())),encoding="utf-8")

path=services.settings.artifact_dir/'released.joblib'
if path.exists():
    bundle=services.joblib.load(path)
    if bundle['kind']=='xgboost':
        import shap  # noqa: F401  warm the native library before the async loop starts
    del bundle
    # Build the immutable feature snapshot before accepting concurrent browser reads.
    # This avoids a cold history calculation competing with chart/API requests.
    services.feature_data()
uvicorn.run('quantpilot.api.app:app',host='127.0.0.1',port=8000)
