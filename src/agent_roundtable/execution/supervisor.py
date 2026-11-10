"""Process supervisor (§27.7): bounded output capture, timeout, cancellation
with a real process-tree kill, and graceful-then-force termination.

Every supervised run:
1. acquires provider capacity from the :class:`RunRegistry`,
2. spawns the child with its OWN process group (never a shell),
3. joins a Windows Job Object so descendants are killable as a tree,
4. reads stdout/stderr concurrently (independent tasks: no pipe deadlock),
5. enforces output caps with truncation metadata,
6. enforces the run timeout and an optional idle timeout,
7. attempts a graceful CTRL_BREAK, then force-terminates the whole job.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agent_roundtable.execution.process_tree import (
    ProcessTree,
    send_ctrl_break,
)
from agent_roundtable.util import utc_now_iso

logger = logging.getLogger("agent_roundtable.execution")

_SUBPROCESS_FLAGS = (
    subprocess.CREATE_NEW_PROCESS_GROUP  # graceful CTRL_BREAK targeting
    if os.name == "nt"
    else 0
)

_GRACE_BEFORE_FORCE_SECONDS = 2.0
_POST_KILL_WAIT_SECONDS = 5.0
_JOB_ASSIGNMENT_TOKEN = b"\x00AGENT_ROUNDTABLE_JOB_ASSIGNED\n"
_BOOTSTRAP_NOT_FOUND = "AR_BOOTSTRAP_EXECUTABLE_NOT_FOUND"

# On Windows, create_subprocess_exec cannot start a process suspended. Launch a
# tiny stdin-gated bootstrap instead: it is assigned to the Job Object before
# the gate is opened, then it starts the requested program with inherited
# stdio. This closes the race where a fast CLI spawns an untracked descendant.
_WINDOWS_BOOTSTRAP = """
import os, subprocess, sys
token = b"\\x00AGENT_ROUNDTABLE_JOB_ASSIGNED\\n"
for expected in token:
    if os.read(0, 1) != bytes((expected,)):
        raise SystemExit(125)
try:
    child = subprocess.Popen(sys.argv[1:], stdin=None, stdout=None, stderr=None, env=os.environ)
except FileNotFoundError:
    os.write(2, b"AR_BOOTSTRAP_EXECUTABLE_NOT_FOUND")
    raise SystemExit(127)
raise SystemExit(child.wait())
"""


class ExecutableNotFoundError(RuntimeError):
    pass


class CapacityExceededError(RuntimeError):
    pass


class _IdleTimeout(Exception):
    """Raised internally when no output arrives within the idle window."""


@dataclass
class SupervisedResult:
    exit_code: int | None = None
    failed_to_start: bool = False
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    idle_timed_out: bool = False
    canceled: bool = False
    terminated: bool = False
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    command: list[str] = field(default_factory=list)
    pid: int | None = None
    supervision_error: str | None = None


class RunRegistry:
    """Max concurrent processes per provider (§27.7; project enforced in M7)."""

    def __init__(self, max_per_agent: dict[str, int] | None = None) -> None:
        self._limits = max_per_agent or {}
        self._active: dict[str, set[str]] = {}

    def acquire(self, agent_id: str, run_id: str) -> None:
        active = self._active.setdefault(agent_id, set())
        if run_id in active:
            return
        limit = self._limits.get(agent_id, 8)
        if len(active) >= limit:
            raise CapacityExceededError(
                f"agent '{agent_id}' has {len(active)}/{limit} active runs"
            )
        active.add(run_id)

    def release(self, agent_id: str, run_id: str) -> None:
        self._active.get(agent_id, set()).discard(run_id)

    def active_count(self, agent_id: str) -> int:
        return len(self._active.get(agent_id, set()))


class Supervisor:
    """Single-run supervisor; state apart from the registry and cancel events."""

    def __init__(self, registry: RunRegistry | None = None) -> None:
        self._registry = registry or RunRegistry()
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._live_trees: dict[str, ProcessTree] = {}

    def registry(self) -> RunRegistry:
        return self._registry

    async def run(
        self,
        command: list[str],
        *,
        run_id: uuid.UUID | str | None = None,
        agent_id: str = "unknown",
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        stdin_data: bytes | None = None,
        timeout_seconds: float = 600.0,
        idle_timeout_seconds: float | None = None,
        max_stdout_bytes: int = 2_000_000,
        max_stderr_bytes: int = 1_000_000,
        cancellation_token: asyncio.Event | None = None,
        on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
    ) -> SupervisedResult:
        run_key = str(run_id) if run_id is not None else str(uuid.uuid4())
        self._registry.acquire(agent_id, run_key)
        result = SupervisedResult(started_at=utc_now_iso(), command=list(command))
        try:
            return await self._supervise(
                command,
                run_key=run_key,
                agent_id=agent_id,
                env=env,
                cwd=cwd,
                stdin_data=stdin_data,
                timeout_seconds=timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
                max_stdout_bytes=max_stdout_bytes,
                max_stderr_bytes=max_stderr_bytes,
                cancellation_token=cancellation_token,
                result=result,
                on_stdout_line=on_stdout_line,
            )
        finally:
            self._registry.release(agent_id, run_key)
            self._cancel_events.pop(run_key, None)
            tree = self._live_trees.pop(run_key, None)
            if tree is not None:
                tree.close()  # KILL_ON_JOB_CLOSE safety net for any survivor

    async def cancel(self, run_id: uuid.UUID | str) -> bool:
        """Signal cancellation; returning False means the run is unknown here."""
        event = self._cancel_events.get(str(run_id))
        if event is None:
            return False
        event.set()
        return True

    # -- internals ----------------------------------------------------------

    async def _supervise(
        self,
        command: list[str],
        *,
        run_key: str,
        agent_id: str,
        env: dict[str, str] | None,
        cwd: str | None,
        stdin_data: bytes | None,
        timeout_seconds: float,
        idle_timeout_seconds: float | None,
        max_stdout_bytes: int,
        max_stderr_bytes: int,
        cancellation_token: asyncio.Event | None,
        result: SupervisedResult,
        on_stdout_line: Callable[[str], Awaitable[None]] | None = None,
    ) -> SupervisedResult:
        cancel_event = cancellation_token or asyncio.Event()
        self._cancel_events[run_key] = cancel_event
        loop = asyncio.get_running_loop()
        started = loop.time()

        try:
            launch_command = command
            launch_stdin = stdin_data
            if os.name == "nt":
                launch_command = [sys.executable, "-c", _WINDOWS_BOOTSTRAP, *command]
                launch_stdin = _JOB_ASSIGNMENT_TOKEN + (stdin_data or b"")
            # Establish the kill-on-close guarantee before launching anything.
            # If Windows rejects the required Job Object policy, no provider
            # process is started without a reliable descendant-kill boundary.
            try:
                tree = ProcessTree()
            except OSError as exc:
                result.finished_at = utc_now_iso()
                result.failed_to_start = True
                result.supervision_error = "process_tree_unavailable"
                result.stderr = f"process supervision unavailable: {exc}"
                return result

            proc = await asyncio.create_subprocess_exec(
                *launch_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
                creationflags=_SUBPROCESS_FLAGS,
                close_fds=True,
            )
        except FileNotFoundError:
            tree.close()
            result.finished_at = utc_now_iso()
            result.failed_to_start = True
            result.exit_code = None
            logger.warning("executable not found: %s", command[0])
            return result
        except OSError as exc:
            tree.close()
            result.finished_at = utc_now_iso()
            result.failed_to_start = True
            result.stderr = f"process launch failed: {exc}"
            result.exit_code = None
            return result

        result.pid = proc.pid
        self._live_trees[run_key] = tree
        if not tree.assign_pid(proc.pid):
            # Do not continue when the process escaped the protected job.
            logger.error(
                "job assignment failed for pid %s; terminating launch", proc.pid
            )
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            result.finished_at = utc_now_iso()
            result.failed_to_start = True
            result.supervision_error = "process_tree_assignment_failed"
            return result

        write_task: asyncio.Task[None] | None = None
        deadline_task: asyncio.Task[None] | None = None
        control_task: asyncio.Task[bool] | None = None
        reader_tasks: list[asyncio.Task[tuple[bytes, bool]]] = []
        try:
            stdout_reader = asyncio.create_task(
                self._read_stream(
                    proc.stdout,
                    max_stdout_bytes,
                    idle_timeout_seconds,
                    on_line=on_stdout_line,
                )
            )
            stderr_reader = asyncio.create_task(
                self._read_stream(proc.stderr, max_stderr_bytes, idle_timeout_seconds)
            )
            reader_tasks.extend((stdout_reader, stderr_reader))
            if launch_stdin is not None:
                write_task = asyncio.create_task(
                    self._write_stdin(proc.stdin, launch_stdin)
                )
            else:
                proc.stdin.close()
            wait_task: asyncio.Task[int] = asyncio.create_task(proc.wait())
            deadline_task: asyncio.Task[None] = asyncio.create_task(
                asyncio.sleep(timeout_seconds)
            )
            control_task: asyncio.Task[bool] = asyncio.create_task(cancel_event.wait())

            pending = {
                stdout_reader,
                stderr_reader,
                wait_task,
                deadline_task,
                control_task,
            }
            outcome = _Outcome()
            readers: dict[asyncio.Task[tuple[bytes, bool]], str] = {
                stdout_reader: "stdout",
                stderr_reader: "stderr",
            }
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                if wait_task in done:
                    break
                if deadline_task in done:
                    outcome.timed_out = True
                    break
                if control_task in done:
                    outcome.canceled = True
                    break
                for task in list(done):
                    exc = task.exception()
                    if isinstance(exc, _IdleTimeout):
                        outcome.idle_timed_out = True
                    elif exc is not None:
                        logger.error("supervision task error", exc_info=exc)
                        if task in readers:
                            outcome.reader_failed = True

            # ---- terminate phase: graceful attempt, then force-kill the tree
            if outcome.requested_termination:
                send_ctrl_break(proc.pid)  # best effort graceful stop
                graceful = False
                try:
                    await asyncio.wait_for(
                        asyncio.shield(wait_task),
                        timeout=_GRACE_BEFORE_FORCE_SECONDS,
                    )
                    graceful = proc.returncode is not None
                except Exception:  # noqa: BLE001 - any error keeps the guarantee intact
                    graceful = False
                if not graceful:
                    tree.terminate()  # kills the ENTIRE job (§27.7)
                    result.terminated = True

            try:
                exit_code = await asyncio.wait_for(
                    asyncio.shield(wait_task), timeout=_POST_KILL_WAIT_SECONDS
                )
                result.exit_code = exit_code
            except (TimeoutError, OSError):
                tree.terminate()
                result.terminated = True
                result.exit_code = None

            # give the pipes a last moment to flush (post-kill they hit EOF)
            await asyncio.wait(set(readers), timeout=2.0)
            for reader, name in readers.items():
                if reader.done() and reader.exception() is None:
                    data, truncated = reader.result()
                    if name == "stdout":
                        result.stdout = data.decode("utf-8", errors="replace")
                        result.stdout_truncated = truncated
                    else:
                        result.stderr = data.decode("utf-8", errors="replace")
                        result.stderr_truncated = truncated
                elif not reader.done():
                    reader.cancel()
            if _BOOTSTRAP_NOT_FOUND in result.stderr:
                result.failed_to_start = True
                result.exit_code = None
                result.supervision_error = "executable_not_found"
                result.stderr = "provider executable could not be started"
            result.finished_at = utc_now_iso()
            result.duration_ms = int((loop.time() - started) * 1000)
            result.canceled = outcome.canceled
            result.timed_out = outcome.timed_out
            result.idle_timed_out = outcome.idle_timed_out
            return result
        finally:
            for task in reader_tasks:
                if not task.done():
                    task.cancel()
            if reader_tasks:
                await asyncio.gather(*reader_tasks, return_exceptions=True)
            # A normally exiting process wins the race against these watchers.
            # Cancel them explicitly so long run timeouts and idle readers do
            # not linger on the event loop after the supervised child exits.
            for task in (deadline_task, control_task):
                if task is not None and not task.done():
                    task.cancel()
            watcher_tasks = [
                task for task in (deadline_task, control_task) if task is not None
            ]
            if watcher_tasks:
                await asyncio.gather(*watcher_tasks, return_exceptions=True)
            self._cancel_events.pop(run_key, None)
            self._live_trees.pop(run_key, None)
            # A CLI can exit successfully while leaving detached descendants in
            # its job. Explicitly terminate survivors before releasing the job
            # handle; KILL_ON_JOB_CLOSE alone is not reliable on every Windows
            # build when the final assigned parent has already exited.
            tree.terminate()
            tree.close()
            if write_task is not None:
                if not write_task.done():
                    write_task.cancel()
                try:
                    write_task.result()
                except (
                    asyncio.CancelledError,
                    BrokenPipeError,
                    ConnectionResetError,
                    OSError,
                ):
                    logger.debug("stdin write ended with error", exc_info=True)

    @staticmethod
    async def _write_stdin(stream: asyncio.StreamWriter | None, data: bytes) -> None:
        """Write the request without blocking output readers, then signal EOF."""
        if stream is None:
            return
        try:
            stream.write(data)
            await stream.drain()
        except (BrokenPipeError, ConnectionResetError):
            # Some CLIs reject a request before consuming all stdin. Their exit
            # status and structured output remain the source of truth.
            pass
        finally:
            stream.close()
            try:
                await stream.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

    async def _read_stream(
        self,
        stream: asyncio.StreamReader | None,
        cap: int,
        idle_timeout_seconds: float | None,
        on_line: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        total = 0
        truncated = False
        line_buffer = b""
        if stream is None:
            return b"", False
        while True:
            try:
                if idle_timeout_seconds is not None:
                    chunk = await asyncio.wait_for(
                        stream.read(65536), timeout=idle_timeout_seconds
                    )
                else:
                    chunk = await stream.read(65536)
            except TimeoutError as exc:
                raise _IdleTimeout from exc
            if not chunk:
                break
            if on_line is not None:
                line_buffer += chunk
                while b"\n" in line_buffer:
                    line_bytes, line_buffer = line_buffer.split(b"\n", 1)
                    line_str = line_bytes.decode("utf-8", errors="replace").rstrip("\r")
                    try:
                        await on_line(line_str)
                    except Exception:
                        logger.warning("on_line callback error", exc_info=True)
            if total + len(chunk) > cap:
                keep = max(cap - total, 0)
                if keep:
                    chunks.append(chunk[:keep])
                    total += keep
                truncated = True
                # keep the loop running: draining the pipe prevents a deadlock
                continue
            chunks.append(chunk)
            total += len(chunk)
        if on_line is not None and line_buffer:
            line_str = line_buffer.decode("utf-8", errors="replace").rstrip("\r")
            try:
                await on_line(line_str)
            except Exception:
                logger.warning("on_line callback error on EOF", exc_info=True)
        return b"".join(chunks), truncated


@dataclass
class _Outcome:
    canceled: bool = False
    timed_out: bool = False
    idle_timed_out: bool = False
    reader_failed: bool = False

    @property
    def requested_termination(self) -> bool:
        return (
            self.canceled or self.timed_out or self.idle_timed_out or self.reader_failed
        )
