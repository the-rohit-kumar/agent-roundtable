"""Single-instance mutex and lifecycle state machine (§26.1, §13.1)."""

from __future__ import annotations

import pytest

from agent_roundtable.lifecycle import (
    AppLifecycle,
    AppState,
    InstanceAlreadyRunning,
    SingleInstanceLock,
    StateTransitionError,
)


def test_single_instance_lock_excludes_second_instance() -> None:
    with SingleInstanceLock(), pytest.raises(InstanceAlreadyRunning):
        lock2 = SingleInstanceLock()
        lock2.acquire()
    # After release, a new lock must be acquirable again.
    lock3 = SingleInstanceLock()
    lock3.acquire()
    lock3.release()


def test_release_without_acquire_is_noop() -> None:
    lock = SingleInstanceLock()
    lock.release()  # must not raise


def test_invalid_state_transition_rejected() -> None:
    lc = AppLifecycle()
    assert lc.state is AppState.STARTING
    with pytest.raises(StateTransitionError):
        lc.transition(AppState.STOPPED)  # STARTING -> STOPPED is illegal


def test_valid_lifecycle_path() -> None:
    lc = AppLifecycle()
    lc.transition(AppState.READY)
    lc.transition(AppState.DEGRADED)
    lc.transition(AppState.STOPPING)
    lc.transition(AppState.STOPPED)
    assert lc.state is AppState.STOPPED


def test_fatal_path() -> None:
    lc = AppLifecycle()
    lc.transition(AppState.FATAL)
    lc.transition(AppState.STOPPED)
    assert lc.state is AppState.STOPPED


def test_provider_states_validated() -> None:
    lc = AppLifecycle()
    lc.set_provider_state("codex", "READY")
    lc.set_provider_state("antigravity", "SANDBOX_UNVERIFIED")
    assert lc.provider_state("codex") == "READY"
    assert lc.provider_state("missing") == "UNKNOWN"
    with pytest.raises(ValueError):
        lc.set_provider_state("codex", "MADE_UP")
    assert lc.degraded is True  # antigravity != READY


def test_provider_ready_not_degraded() -> None:
    lc = AppLifecycle()
    lc.set_provider_state("codex", "READY")
    lc.set_provider_state("antigravity", "READY")
    assert lc.degraded is False
