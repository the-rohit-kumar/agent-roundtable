"""Windows Job Object process-tree management (ctypes; DECISIONS D3, §27.7).

Killing only the parent CLI process leaves grandchildren alive and (for Codex)
the shared app-server daemon detached — therefore every supervised run is a
Job Object member from launch: `TerminateJobObject` kills the whole tree, and
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` guarantees nothing survives if the
controller itself dies (§27.7 "no hidden process left running").
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Self

logger = logging.getLogger("agent_roundtable.execution")

_COMPILED = False

#: JobObjectExtendedLimitInformation (class 9)
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_ProcessSuspendResume = 0x00000800  # present in later SDKs; use our own const
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000200

_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3

_OPEN_ACCESS = 0x1F0FFF  # PROCESS_ALL_ACCESS (sufficient for Query/Assign)
_PROCESS_QUERY_LIMITED = 0x0400
_SYNCHRONIZE = 0x00100000

_CTRL_BREAK_EVENT = 1
_EXIT_CODE_KILL = 0xDEADBE0F

#: marker exit code returned by TerminateJobObject kills (documented constant)

fastcall = ctypes.WinDLL("kernel32", use_last_error=True)


def _configure_prototypes() -> None:
    global _COMPILED
    if _COMPILED:
        return
    fastcall.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    fastcall.CreateJobObjectW.restype = wintypes.HANDLE
    fastcall.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    fastcall.SetInformationJobObject.restype = wintypes.BOOL
    fastcall.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    fastcall.QueryInformationJobObject.restype = wintypes.BOOL
    fastcall.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    fastcall.AssignProcessToJobObject.restype = wintypes.BOOL
    fastcall.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    fastcall.TerminateJobObject.restype = wintypes.BOOL
    fastcall.CloseHandle.argtypes = [wintypes.HANDLE]
    fastcall.CloseHandle.restype = wintypes.BOOL
    fastcall.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    fastcall.OpenProcess.restype = wintypes.HANDLE
    fastcall.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    fastcall.WaitForSingleObject.restype = wintypes.DWORD
    fastcall.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
    fastcall.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
    _COMPILED = True


class JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
    _fields_ = [
        ("NumberOfAssignedProcesses", wintypes.DWORD),
        ("NumberOfProcessIdsInList", wintypes.DWORD),
        ("ProcessIdList", ctypes.POINTER(ctypes.c_size_t)),
    ]


def _extended_limit_info() -> ctypes.Structure:
    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(f"_{i}", ctypes.c_ulonglong) for i in range(6)]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_size_t)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    return info


class ProcessTree:
    """A Job Object handle bound to exactly one supervised process-tree."""

    def __init__(self) -> None:
        _configure_prototypes()
        self._handle: wintypes.HANDLE = fastcall.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(f"CreateJobObjectW failed: {ctypes.get_last_error()}")
        info = _extended_limit_info()
        ok = fastcall.SetInformationJobObject(
            self._handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            self.close()
            raise OSError(f"SetInformationJobObject failed: {ctypes.get_last_error()}")
        self._terminated = False

    def assign_pid(self, pid: int) -> bool:
        """Add an existing process to the job (used right after spawn)."""
        if self._terminated:
            return False
        h = fastcall.OpenProcess(_OPEN_ACCESS, False, int(pid))
        if not h:
            logger.debug("OpenProcess(%s) failed: %s", pid, ctypes.get_last_error())
            return False
        try:
            ok = bool(fastcall.AssignProcessToJobObject(self._handle, h))
            if not ok:
                logger.debug(
                    "AssignProcessToJobObject(%s) failed: %s",
                    pid,
                    ctypes.get_last_error(),
                )
            return ok
        finally:
            fastcall.CloseHandle(h)

    def active_pids(self, capacity: int = 256) -> list[int]:
        """PIDs currently alive inside this job. Empty when the tree is gone."""
        if self._terminated or not self._handle:
            return []

        class _Layout(ctypes.Structure):
            _fields_ = [
                ("NumberOfAssignedProcesses", wintypes.DWORD),
                ("NumberOfProcessIdsInList", wintypes.DWORD),
                ("ProcessIdList", ctypes.c_size_t * capacity),
            ]

        buf = _Layout()
        returned = wintypes.DWORD(0)
        ok = fastcall.QueryInformationJobObject(
            self._handle,
            _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
            ctypes.byref(buf),
            ctypes.sizeof(buf),
            ctypes.byref(returned),
        )
        if not ok:
            return []
        return list(buf.ProcessIdList[: buf.NumberOfProcessIdsInList])

    def terminate(self, exit_code: int = _EXIT_CODE_KILL) -> bool:
        """Kill every process in the job, present and future."""
        if self._terminated or not self._handle:
            return False
        pids = self.active_pids()
        ok = bool(fastcall.TerminateJobObject(self._handle, exit_code))
        self._terminated = True
        if not ok:
            logger.debug("TerminateJobObject failed: %s", ctypes.get_last_error())
            return False
        # TerminateJobObject initiates process termination asynchronously. Wait
        # for each member so callers can safely report that the tree is gone.
        for pid in pids:
            process = fastcall.OpenProcess(_SYNCHRONIZE, False, int(pid))
            if process:
                try:
                    fastcall.WaitForSingleObject(process, 3000)
                finally:
                    fastcall.CloseHandle(process)
        return ok

    def close(self) -> None:
        if self._handle:
            fastcall.CloseHandle(self._handle)
            self._handle = wintypes.HANDLE(0)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def process_alive(pid: int) -> bool:
    """Best-effort liveness probe used by tests and/stop verification."""
    _configure_prototypes()
    h = fastcall.OpenProcess(_PROCESS_QUERY_LIMITED | _SYNCHRONIZE, False, int(pid))
    if not h:
        return False  # cannot query => treat as gone
    try:
        WAIT_TIMEOUT = 0x00000102
        return fastcall.WaitForSingleObject(h, 0) == WAIT_TIMEOUT
    finally:
        fastcall.CloseHandle(h)


def send_ctrl_break(pid: int) -> bool:
    """Best-effort graceful signal to a console process group (§27.7)."""
    _configure_prototypes()
    try:
        return bool(fastcall.GenerateConsoleCtrlEvent(_CTRL_BREAK_EVENT, int(pid)))
    except Exception:  # noqa: BLE001 - best-effort; force kill follows
        return False
