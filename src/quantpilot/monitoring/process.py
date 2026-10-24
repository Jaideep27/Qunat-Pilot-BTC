"""The current process's creation time, recorded by service runtime files.

`scripts/ops/stop_data_supervisor.py` compares a recorded PID's OS creation time with the record before
stopping it. Recording `time.time()` after slow imports could differ by more than its 30-second
tolerance on a memory-starved machine and block a legitimate restart.
"""
from __future__ import annotations

import os
import time


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
