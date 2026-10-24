"""Stop only this workspace's recorded supervisor before a user-launched restart."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys


def main():
    root=Path(__file__).resolve().parents[2]
    service='api' if '--api' in sys.argv else 'data-supervisor'
    record=root/'data'/f'{service}-runtime.json'
    if not record.exists():return
    runtime=json.loads(record.read_text(encoding='utf-8-sig'))
    pid=int(runtime['pid'])
    if pid<=0 or pid==os.getpid():raise RuntimeError('Invalid supervisor PID')
    if os.name!='nt':raise RuntimeError('Automatic supervisor restart is supported on Windows only')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
    kernel.GetProcessTimes.restype=wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype=wintypes.BOOL
    kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
    kernel.TerminateProcess.restype=wintypes.BOOL
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    checking='--check' in sys.argv
    handle=kernel.OpenProcess(0x1000|(0 if checking else 0x0001|0x00100000),False,pid)
    if not handle:
        if ctypes.get_last_error()==87:return  # Process has already exited.
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        name=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(name))
        if not kernel.QueryFullProcessImageNameW(handle,0,name,ctypes.byref(size)):
            # The recorded PID was reused by a process we cannot inspect, so it is not ours.
            print(f'Recorded {service} PID {pid} now belongs to an inaccessible process; nothing stopped.')
            return
        executable=Path(name.value).resolve()
        allowed=any(executable.is_relative_to(root/directory) for directory in ('.venv','.python'))
        if not allowed or executable.name.lower()!='python.exe':
            raise RuntimeError('Recorded PID is not this workspace Python; refusing to stop it')
        creation,exit_time,kernel_time,user_time=(wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle,*map(ctypes.byref,(creation,exit_time,kernel_time,user_time))):
            raise ctypes.WinError(ctypes.get_last_error())
        started=((creation.dwHighDateTime<<32)|creation.dwLowDateTime)/10_000_000-11644473600
        if abs(started-float(runtime['started']))>30:
            raise RuntimeError('Recorded PID was reused; refusing to stop it')
        if checking:
            print(f'Ownership and creation time verified for project {service} (PID {pid}); no process stopped.')
            return
        if not kernel.TerminateProcess(handle,0):raise ctypes.WinError(ctypes.get_last_error())
        if kernel.WaitForSingleObject(handle,10000)!=0:raise RuntimeError('Supervisor did not exit')
        print(f'Previous project {service} stopped. PostgreSQL and stored data are preserved.')
    finally:kernel.CloseHandle(handle)


if __name__=='__main__':main()
