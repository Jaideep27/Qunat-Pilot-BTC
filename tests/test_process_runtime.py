"""Service runtime records carry the OS process creation time, so restarts can verify ownership."""
import os
import subprocess
import time
from pathlib import Path

import pytest

from quantpilot.process import process_started


def test_process_started_is_the_os_creation_time_not_the_time_imports_finished():
    started = process_started()
    assert started <= time.time()
    if os.name == 'nt':
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              f'([DateTimeOffset](Get-Process -Id {os.getpid()}).StartTime).ToUnixTimeMilliseconds()'],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        assert abs(started-int(out)/1000) < 1.0


@pytest.mark.parametrize('script', ['scripts/data_supervisor.py', 'scripts/serve_api.py'])
def test_service_scripts_record_the_creation_time(script):
    source = Path(script).read_text(encoding='utf-8')
    assert 'started=process_started()' in source and 'started=time.time()' not in source
