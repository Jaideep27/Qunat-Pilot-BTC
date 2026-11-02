"""The API launcher must not overwrite the runtime record when port 8000 is already served: a failed second start
used to leave a record of a dead process, so every later restart stopped nothing and the old API kept serving."""
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_a_second_api_start_leaves_the_running_apis_record_alone(tmp_path):
    held = socket.socket()
    try:
        held.bind(('127.0.0.1', 8000))
        held.listen(1)
    except OSError:
        held.close()
        held = None                      # the live API already serves the port: the same situation
    try:
        env = dict(os.environ, QUANTPILOT_DATA_DIR=str(tmp_path), QUANTPILOT_ARTIFACT_DIR=str(tmp_path/'none'),
                   PYTHONPATH=str(ROOT/'src'))
        run = subprocess.run([sys.executable, str(ROOT/'scripts'/'ops'/'serve_api.py')], cwd=tmp_path, env=env,
                             capture_output=True, text=True, timeout=120)
    finally:
        if held is not None:
            held.close()
    assert run.returncode != 0 and 'already' in (run.stdout+run.stderr)
    assert not (tmp_path/'api-runtime.json').exists()
