"""Windows Job Object self-assign for per-process memory caps.

Uses ctypes against kernel32.dll directly — no pywin32 dependency.
Supported on Windows 8+ (nested Job Objects). hankpdf already requires
Python 3.14, which dropped Windows 7/8 upstream, so this matches the
supported platform floor.

Mechanism:

1. CreateJobObjectW(NULL, NULL) -> hJob.
2. SetInformationJobObject with JobObjectExtendedLimitInformation
   carrying ProcessMemoryLimit and the JOB_OBJECT_LIMIT_PROCESS_MEMORY
   + JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE flags.
3. AssignProcessToJobObject(hJob, GetCurrentProcess()).
4. Intentionally leak hJob: closing it would kill us due to
   KILL_ON_JOB_CLOSE. The kernel releases the handle when the
   process exits.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_void_p),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9


def apply(byte_limit: int) -> None:
    """Self-assign the current process to a Job Object with a memory cap."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.CreateJobObjectW.restype = wt.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    h_job = kernel32.CreateJobObjectW(None, None)
    if not h_job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW")

    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_PROCESS_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    info.ProcessMemoryLimit = ctypes.c_size_t(byte_limit)

    kernel32.SetInformationJobObject.restype = wt.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wt.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wt.DWORD,
    ]
    if not kernel32.SetInformationJobObject(
        h_job,
        _JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject")

    kernel32.GetCurrentProcess.restype = wt.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    h_proc = kernel32.GetCurrentProcess()

    kernel32.AssignProcessToJobObject.restype = wt.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    if not kernel32.AssignProcessToJobObject(h_job, h_proc):
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject")
    # Intentionally leak h_job — closing kills us via KILL_ON_JOB_CLOSE.
