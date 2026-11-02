"""Start the local API on 127.0.0.1:8000 and record its process, so restarts stop the right one."""
import json
import os
import socket

import uvicorn

from quantpilot.config import settings
from quantpilot.monitoring.process import process_started

# A second start must not overwrite the running API's record: bind would fail, the record would name a dead
# process, and every later restart would stop nothing while the old API kept serving old code (2026-11-02).
with socket.socket() as probe:
    probe.settimeout(2)
    if probe.connect_ex(('127.0.0.1', 8000)) == 0:
        raise SystemExit('Port 8000 is already served by a running API; its runtime record is left unchanged.')
settings.data_dir.mkdir(exist_ok=True, parents=True)
(settings.data_dir/'api-runtime.json').write_text(json.dumps(dict(pid=os.getpid(), started=process_started())),
                                                   encoding='utf-8')
uvicorn.run('quantpilot.api.app:app', host='127.0.0.1', port=8000)
