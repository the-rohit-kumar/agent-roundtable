from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import agent_roundtable.execution.supervisor as supervisor_module
from agent_roundtable.execution.process_tree import ProcessTree, process_alive
from agent_roundtable.execution.supervisor import (
    CapacityExceededError,
    RunRegistry,
    Supervisor,
)


@pytest.fixture
def fake_process_tree(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Exercise supervisor control flow independent of host Job Object policy."""

    class _FakeTree:
        def __init__(self) -> None:
            self.pid: int | None = None

        def assign_pid(self, pid: int) -> bool:
            self.pid = pid
            return True

        def terminate(self) -> bool:
            if self.pid is None:
                return False
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self.pid)],
                capture_output=True,
                check=False,
                timeout=5,
            )
            return True

        def close(self) -> None:
            return None

    monkeypatch.setattr(supervisor_module, "ProcessTree", _FakeTree)
    yield


@pytest.mark.asyncio
async def test_supervisor_feeds_stdin_and_captures_result(
    fake_process_tree: None,
) -> None:
    supervisor = Supervisor()
    result = await supervisor.run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read())"],
        stdin_data=b"Roundtable prompt\nwith multiple lines",
        timeout_seconds=10,
    )
    assert result.exit_code == 0
    assert result.stdout.replace("\r\n", "\n").strip() == (
        "Roundtable prompt\nwith multiple lines"
    )
    assert not result.failed_to_start


@pytest.mark.asyncio
async def test_supervisor_drains_both_pipes_and_caps_capture(
    fake_process_tree: None,
) -> None:
    code = "import sys; sys.stdout.write('o'*200000); sys.stderr.write('e'*200000)"
    result = await Supervisor().run(
        [sys.executable, "-c", code],
        timeout_seconds=10,
        max_stdout_bytes=4096,
        max_stderr_bytes=2048,
    )
    assert result.exit_code == 0
    assert len(result.stdout) == 4096
    assert len(result.stderr) == 2048
    assert result.stdout_truncated and result.stderr_truncated


@pytest.mark.asyncio
async def test_supervisor_terminates_timeout_and_cancellation(
    fake_process_tree: None,
) -> None:
    supervisor = Supervisor()
    timed = await supervisor.run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=0.2,
    )
    assert timed.timed_out

    run_id = "cancel-me"
    pending = asyncio.create_task(
        supervisor.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            run_id=run_id,
            timeout_seconds=30,
        )
    )
    for _ in range(100):
        if run_id in supervisor._cancel_events:
            break
        await asyncio.sleep(0.01)
    assert await supervisor.cancel(run_id)
    canceled = await asyncio.wait_for(pending, timeout=10)
    assert canceled.canceled


@pytest.mark.asyncio
async def test_supervisor_reports_missing_executable_and_releases_capacity(
    fake_process_tree: None,
) -> None:
    registry = RunRegistry({"codex": 1})
    supervisor = Supervisor(registry)
    result = await supervisor.run(
        ["this-executable-does-not-exist-roundtable"],
        agent_id="codex",
        timeout_seconds=5,
    )
    assert result.failed_to_start
    assert result.exit_code is None
    assert registry.active_count("codex") == 0


def test_run_registry_enforces_per_agent_limit() -> None:
    registry = RunRegistry({"codex": 1})
    registry.acquire("codex", "first")
    with pytest.raises(CapacityExceededError):
        registry.acquire("codex", "second")
    registry.release("codex", "first")
    registry.acquire("codex", "second")


def test_windows_job_object_kill_on_close_policy_is_available() -> None:
    try:
        tree = ProcessTree()
    except OSError as exc:
        pytest.skip(f"host does not permit required Job Object policy: {exc}")
    tree.close()


@pytest.mark.asyncio
async def test_job_object_close_kills_stubborn_grandchild(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    child_code = (
        "import os,time;"
        f"open({str(child_pid_file)!r},'w').write(str(os.getpid()));"
        "time.sleep(30)"
    )
    parent_code = (
        "import os,subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}],"
        "creationflags=0x00000008|0x00000200,close_fds=True)\n"
        f"deadline=time.monotonic()+5; path={str(child_pid_file)!r}\n"
        "while not os.path.exists(path) and time.monotonic()<deadline:\n"
        "    time.sleep(0.02)\n"
        "assert os.path.exists(path), 'grandchild did not start'\n"
    )
    result = await Supervisor().run(
        [sys.executable, "-c", parent_code],
        timeout_seconds=10,
    )
    assert result.exit_code == 0
    assert child_pid_file.is_file(), (
        f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    )
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    assert not process_alive(child_pid)


@pytest.mark.asyncio
async def test_supervisor_refuses_to_launch_without_job_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unavailable() -> None:
        raise OSError("required kill-on-close policy unavailable")

    monkeypatch.setattr(supervisor_module, "ProcessTree", _unavailable)
    result = await Supervisor().run(
        [sys.executable, "-c", "raise SystemExit(99)"], timeout_seconds=2
    )
    assert result.failed_to_start
    assert result.supervision_error == "process_tree_unavailable"
    assert result.pid is None
