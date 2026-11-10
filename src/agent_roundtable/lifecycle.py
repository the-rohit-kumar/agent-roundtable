"""Runtime lifecycle: single-instance lock and app state machine (§26.1–26.3)."""

from __future__ import annotations

import ctypes
import sys
import time
from enum import StrEnum
from typing import Self

if sys.platform == "win32":  # pragma: no cover - platform-specific import
    import ctypes.wintypes as wt


class InstanceAlreadyRunning(RuntimeError):
    """Another controller instance holds the single-instance mutex."""


class StateTransitionError(RuntimeError):
    """An invalid application state transition was requested."""


class AppState(StrEnum):
    STARTING = "starting"
    DEGRADED = "degraded"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FATAL = "fatal"


_VALID_TRANSITIONS: dict[AppState, set[AppState]] = {
    AppState.STARTING: {
        AppState.DEGRADED,
        AppState.READY,
        AppState.STOPPING,
        AppState.FATAL,
    },
    AppState.DEGRADED: {
        AppState.READY,
        AppState.DEGRADED,
        AppState.STOPPING,
        AppState.FATAL,
    },
    AppState.READY: {AppState.DEGRADED, AppState.STOPPING, AppState.FATAL},
    AppState.STOPPING: {AppState.STOPPED},
    AppState.STOPPED: set(),
    AppState.FATAL: {AppState.STOPPED},
}


class SingleInstanceLock:
    """Windows named mutex enforced process-wide (DECISIONS D4)."""

    _MUTEX_NAME = "Global\\AgentRoundtable.Controller.Instance"
    _ERROR_ALREADY_EXISTS = 183

    def __init__(self) -> None:
        self._handle: int | None = None

    def acquire(self) -> None:
        if sys.platform != "win32":
            # Tests may run on other platforms; emulate via module-level flag.
            if getattr(SingleInstanceLock, "_acquired", False):
                raise InstanceAlreadyRunning("Another instance is running")
            SingleInstanceLock._acquired = True
            self._handle = -1
            return

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]  # type: ignore[attr-defined]

        handle = kernel32.CreateMutexW(None, False, self._MUTEX_NAME)
        last_error = ctypes.get_last_error()
        if not handle:
            raise OSError(f"CreateMutexW failed: {last_error}")
        if last_error == self._ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            raise InstanceAlreadyRunning(
                "Another Agent Roundtable controller instance is already running"
            )
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle(ctypes.c_void_p(self._handle))
        else:
            SingleInstanceLock._acquired = False  # type: ignore[attr-defined]
        self._handle = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


class AppLifecycle:
    """Tracks the application state machine with validated transitions (§26.1)."""

    def __init__(self) -> None:
        self._state = AppState.STARTING
        self._provider_states: dict[str, str] = {}
        self._started_monotonic = time.monotonic()
        self.discord_connected = False

    @property
    def uptime_seconds(self) -> int:
        return max(0, int(time.monotonic() - self._started_monotonic))

    @property
    def state(self) -> AppState:
        return self._state

    def transition(self, new_state: AppState) -> None:
        if new_state not in _VALID_TRANSITIONS[self._state]:
            raise StateTransitionError(
                f"Invalid transition {self._state.value} -> {new_state.value}"
            )
        self._state = new_state

    def set_provider_state(self, agent_id: str, state: str) -> None:
        allowed = {
            "UNKNOWN",
            "READY",
            "AUTH_REQUIRED",
            "QUOTA_BLOCKED",
            "SANDBOX_UNVERIFIED",
            "UNAVAILABLE",
            "DISABLED",
            "CLI_MISSING",
            "AUTH_UNKNOWN",
            "AUTH_EXPIRED",
            "CLI_AVAILABLE",
            "READ_ONLY",
        }
        if state not in allowed:
            raise ValueError(f"Unknown provider state: {state}")
        self._provider_states[agent_id] = state

    def provider_state(self, agent_id: str) -> str:
        return self._provider_states.get(agent_id, "UNKNOWN")

    @property
    def degraded(self) -> bool:
        return bool(self._provider_states) and any(
            s != "READY" for s in self._provider_states.values()
        )
