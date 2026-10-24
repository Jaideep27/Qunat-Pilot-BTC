"""Warm trusted native model libraries before starting Windows' async HTTP loop."""
from quantpilot.api import services
from quantpilot.monitoring.process import process_started
import uvicorn
import json
import os

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
