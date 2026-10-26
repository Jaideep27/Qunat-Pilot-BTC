"""Process helpers for the local services: creation time for runtime records, and hidden starts.

`process_started` is the current process's creation time, recorded by service runtime files.

`scripts/ops/stop_data_supervisor.py` compares a recorded PID's OS creation time with the record before
stopping it. Recording `time.time()` after slow imports could differ by more than its 30-second
tolerance on a memory-starved machine and block a legitimate restart.
"""
from __future__ import annotations

import os
import subprocess
import time

# A console of its own with no window: programs the service starts share it instead of opening visible terminals.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def process_started():
    """Seconds since the epoch at which the OS created this process (Windows); `time.time()` elsewhere."""
    if os.name != 'nt':
        return time.time()
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)]*4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), *map(ctypes.byref, times)):
        return time.time()
    creation = times[0]
    return ((creation.dwHighDateTime << 32) | creation.dwLowDateTime)/10_000_000-11644473600


def start_hidden(args, log_path, cwd):
    """Start a long-running service with its output appended to `log_path`, without any console window.

    Unlike a detached start, the service keeps a (windowless) console, so the console programs it starts,
    such as each PostgreSQL backend, reuse it rather than each opening a terminal. Its own process group
    keeps Ctrl+C in another window from reaching it. Returns the Popen object.
    """
    flags = NO_WINDOW | (subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
    with open(log_path, 'ab') as log:
        return subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags,
                                start_new_session=os.name != 'nt')
