"""Service runtime records carry the OS process creation time, so restarts can verify ownership."""
import os
import sys
import subprocess
import time
from pathlib import Path

import pytest

from quantpilot.monitoring.process import process_started


def test_process_started_is_the_os_creation_time_not_the_time_imports_finished():
    started = process_started()
    assert started <= time.time()
    if os.name == 'nt':
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              f'([DateTimeOffset](Get-Process -Id {os.getpid()}).StartTime).ToUnixTimeMilliseconds()'],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        assert abs(started-int(out)/1000) < 1.0


@pytest.mark.parametrize('script', ['scripts/ops/data_supervisor.py', 'scripts/ops/serve_api.py'])
def test_service_scripts_record_the_creation_time(script):
    source = Path(script).read_text(encoding='utf-8')
    assert 'started=process_started()' in source and 'started=time.time()' not in source


# A background service started without a console (Node's `detached`) makes Windows open a new, visible console
# window for every console program it starts: each PostgreSQL backend got its own terminal at logon, and closing
# one killed that backend (exit 0xC000013A), which made PostgreSQL reset every connection.
GRANDCHILD_WINDOW = ("import subprocess,sys;print(subprocess.run([sys.executable,'-c',"
                     "'import ctypes;print(ctypes.windll.kernel32.GetConsoleWindow())'],"
                     "capture_output=True,text=True).stdout.strip(),flush=True)")


@pytest.mark.skipif(os.name != 'nt', reason='console windows are a Windows concern')
def test_start_hidden_gives_grandchildren_a_shared_console_with_no_window(tmp_path):
    from quantpilot.monitoring.process import start_hidden
    proc = start_hidden([sys.executable, '-c', GRANDCHILD_WINDOW], tmp_path/'child.log', tmp_path)
    assert proc.wait(timeout=60) == 0
    assert (tmp_path/'child.log').read_text(encoding='utf-8').strip() == '0'


@pytest.mark.skipif(os.name != 'nt', reason='console windows are a Windows concern')
def test_start_hidden_script_leaves_the_service_running_after_it_exits(tmp_path):
    import ctypes
    out = subprocess.run([sys.executable, 'scripts/ops/start_hidden.py', str(tmp_path/'svc.log'), '--',
                          sys.executable, '-c', 'import time;time.sleep(60)'],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    pid = int(out.stdout.strip())
    kernel = ctypes.WinDLL('kernel32')
    handle = kernel.OpenProcess(0x1000 | 0x0001, False, pid)
    try:
        code = ctypes.c_ulong()
        assert handle and kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259  # STILL_ACTIVE
    finally:
        if handle:
            kernel.TerminateProcess(handle, 0)
            kernel.CloseHandle(handle)


def test_launcher_starts_services_hidden_not_detached():
    source = Path('scripts/start.cjs').read_text(encoding='utf-8')
    assert 'scripts/ops/start_hidden.py' in source and 'detached:true' not in source
