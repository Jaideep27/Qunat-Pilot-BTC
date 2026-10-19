"""Warm trusted native model libraries before starting Windows' async HTTP loop."""
from quantpilot import services
import uvicorn
import json
import os
import time

services.settings.data_dir.mkdir(exist_ok=True,parents=True)
(services.settings.data_dir/'api-runtime.json').write_text(json.dumps(dict(pid=os.getpid(),started=time.time())),encoding='utf-8')

path=services.settings.artifact_dir/'released.joblib'
if path.exists():
    bundle=services.joblib.load(path)
    if bundle['kind']=='xgboost':
        import shap
    del bundle
    # Build the immutable feature snapshot before accepting concurrent browser reads.
    # This avoids a cold history calculation competing with chart/API requests.
    services.feature_data()
uvicorn.run('quantpilot.api:app',host='127.0.0.1',port=8000)
