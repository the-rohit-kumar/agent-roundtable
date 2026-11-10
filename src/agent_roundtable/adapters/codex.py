"""Codex adapter (§27.5) — built ONLY on the verified v0.166.0 CLI surface
(docs/milestone0/codex_cli_surface.md).

Command shape (top-level `--no-daemon` is REQUIRED before `exec` — DECISIONS
D12: without it exec delegates to the shared daemon and cannot be supervised)::

    codex --no-daemon exec --json --sandbox <profile> [--ephemeral]
    [--skip-git-repo-check] [-C <dir>] -     (prompt on stdin)

Prohibited (fail closed): `danger-full-access`, `--worktree`,
`--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`,
any bypass-equivalent mode.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

from agent_roundtable.adapters import env as env_allowlist
from agent_roundtable.adapters.parsers import (
    CODEX_PARSER_VERSION,
    classify_provider_pause,
    codex_status,
    parse_codex_events,
)
from agent_roundtable.domain.models import (
    AdapterNotAvailableError,
    AgentCapabilities,
    AgentHealth,
    AgentRequest,
    AgentRunResult,
    AgentRunStatus,
    RequestPolicyError,
)
from agent_roundtable.execution.supervisor import SupervisedResult, Supervisor
from agent_roundtable.observability.logging import redact

logger = logging.getLogger("agent_roundtable.adapters.codex")

#: progress stream lines that are known harness artifacts, not diagnostics
_KNOWN_STDERR_NOISE_LINES = ("Reading", "warning: no session file")


class CodexAdapter:
    agent_id = "codex"

    def __init__(
        self,
        *,
        supervisor: Supervisor,
        executable: str = "codex",
        write_verified: bool = False,
        available: bool = True,
        env_overrides: dict[str, str] | None = None,
        wrapper: list[str] | None = None,  # test-only injection point
        test_mode: bool = False,
    ) -> None:
        self._supervisor = supervisor
        self._executable = executable
        self._write_verified = write_verified
        self._available = available
        self._env_overrides = env_overrides or {}
        self._wrapper = list(wrapper or [])
        self._test_mode = test_mode
        self._paused_reason: str | None = None

    # -- Adapter protocol ---------------------------------------------------

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            agent_id=self.agent_id,
            read_only_supported=self._available,
            sandboxed_write_supported=self._write_verified,
            # The CLI supports resume, but this adapter does not expose it yet.
            # Report only capabilities that the adapter can currently perform.
            stateful_resume_supported=False,
            structured_output_supported=True,  # --output-schema verified in M0
            notes="codex-cli 0.166.0 top-level --no-daemon exec --json; prompt via stdin '-'",
        )

    def operational_status(self) -> str:
        """Report local state without starting a provider request."""
        if self._paused_reason == "quota_exhausted":
            return "QUOTA_BLOCKED"
        if self._paused_reason:
            return "AUTH_EXPIRED"
        if not self._available:
            return "DISABLED"
        if not find_executable(self._executable):
            return "CLI_MISSING"
        return "CLI_AVAILABLE" if self._write_verified else "READ_ONLY"

    async def health_check(self) -> AgentHealth:
        if self._paused_reason is not None:
            return AgentHealth(
                agent_id=self.agent_id,
                state="DEGRADED",
                reason_code=self._paused_reason.upper(),
                detail="provider paused pending interactive recovery",
            )
        if not self._available:
            return AgentHealth(
                agent_id=self.agent_id,
                state="DISABLED",
                reason_code="CONFIG_DISABLED",
                detail="adapter disabled by configuration (fail closed)",
            )
        if not find_executable(self._executable):
            return AgentHealth(
                agent_id=self.agent_id,
                state="DISABLED",
                reason_code="EXECUTABLE_NOT_FOUND",
                detail=f"'{self._executable}' not found",
            )
        request = AgentRequest(
            agent_id="codex",
            purpose="diagnostic",
            permission_profile="read_only",
            prompt=(
                "harness healthcheck: reply with exactly ROUNDTABLE_HEALTHCHECK_OK"
            ),
            timeout_seconds=60,
        )
        result = await self.run(request)
        if result.status is AgentRunStatus.SUCCESS:
            return AgentHealth(agent_id=self.agent_id, state="READY")
        return AgentHealth(
            agent_id=self.agent_id,
            state="DEGRADED",
            reason_code=f"HEALTHCHECK_{result.status.value.upper()}",
            detail=(result.response_text or result.reason_code or "")[:200],
        )

    async def run(
        self,
        request: AgentRequest,
        *,
        on_stream_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentRunResult:
        started = time.monotonic()
        if not self._available:
            raise AdapterNotAvailableError(
                "codex adapter is disabled; refusing to run (fail closed)"
            )
        if self._paused_reason is not None:
            raise AdapterNotAvailableError(
                f"codex provider paused ({self._paused_reason}); interactive recovery required"
            )
        _validate_profile(request, write_verified=self._write_verified)
        command = self._build_command(request)
        child_env = env_allowlist.build_child_env(
            extra_documents=self._env_overrides,
            include_test_payload_vars=self._test_mode,
        )

        async def _on_line(line: str) -> None:
            if on_stream_delta is None:
                return
            stripped = line.strip()
            if not stripped:
                return
            try:
                event = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                return
            if not isinstance(event, dict):
                return
            event_type = event.get("type")
            if event_type == "item.completed":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        await on_stream_delta(text)

        raw = await self._supervisor.run(
            command,
            run_id=request.run_id,
            agent_id=request.agent_id,
            env=child_env,
            cwd=request.working_directory,
            stdin_data=request.prompt.encode("utf-8"),
            timeout_seconds=float(request.timeout_seconds),
            max_stdout_bytes=request.max_stdout_bytes,
            max_stderr_bytes=request.max_stderr_bytes,
            on_stdout_line=_on_line if on_stream_delta is not None else None,
        )
        return self._map_result(request, raw, started)

    async def cancel(self, run_id: UUID) -> bool:
        return await self._supervisor.cancel(run_id)

    # -- internals -----------------------------------------------------------

    def _build_command(self, request: AgentRequest) -> list[str]:
        command = [
            *self._wrapper,
            self._executable,
            "--no-daemon",  # DECISIONS D12: supervisable child, not the daemon
            "exec",
            "--json",
            "--sandbox",
            _sandbox_flag(request),
        ]
        if request.permission_profile in ("discussion", "read_only"):
            command += ["--ephemeral", "--skip-git-repo-check"]
        if request.working_directory is not None:
            command += ["-C", request.working_directory]
        command += ["-"]  # prompt via stdin (captured surface, fixture note)
        return command

    def _map_result(
        self, request: AgentRequest, raw: SupervisedResult, started: float
    ) -> AgentRunResult:
        if raw.failed_to_start:
            return self._finish(
                request,
                raw,
                started,
                status=AgentRunStatus.ERROR,
                reason_code=raw.supervision_error or "executable_not_found",
            )
        parse = parse_codex_events(raw.stdout)
        pause_reason = classify_provider_pause(raw.stderr)
        status_text, reason_text = codex_status(
            parse,
            exit_code=raw.exit_code,
            timed_out=raw.timed_out or raw.idle_timed_out,
            canceled=raw.canceled,
        )
        if pause_reason is not None:
            self._paused_reason = pause_reason
            status_text, reason_text = "blocked", pause_reason
        if raw.stdout_truncated and status_text == "success":
            status_text, reason_text = "unknown", "stdout_truncated"
        diagnostics = parse.diagnostics + [
            line
            for line in raw.stderr.splitlines()
            if line.strip() and not line.startswith(_KNOWN_STDERR_NOISE_LINES)
        ]
        diagnostics = [redact(line) for line in diagnostics]
        return self._finish(
            request,
            raw,
            started,
            status=AgentRunStatus(status_text),
            response_text=parse.response_text if status_text == "success" else None,
            provider_session_id=parse.provider_session_id,
            usage=parse.usage,
            diagnostics=diagnostics,
            reason_code=reason_text,
            parser_version=CODEX_PARSER_VERSION,
        )

    def _finish(
        self,
        request: AgentRequest,
        raw: SupervisedResult,
        started: float,
        *,
        status: AgentRunStatus,
        response_text: str | None = None,
        provider_session_id: str | None = None,
        usage: dict | None = None,
        diagnostics: list[str] | None = None,
        reason_code: str | None = None,
        parser_version: str = "codex-adapter-internal",
    ) -> AgentRunResult:
        return AgentRunResult(
            run_id=request.run_id,
            agent_id=request.agent_id,
            status=status,
            response_text=response_text,
            provider_session_id=provider_session_id,
            provider_status=None,
            exit_code=raw.exit_code,
            started_at=raw.started_at,
            finished_at=raw.finished_at,
            duration_ms=int((time.monotonic() - started) * 1000) or raw.duration_ms,
            usage=dict(usage or {}),
            diagnostics=list(diagnostics or []),
            reason_code=reason_code,
            parser_version=parser_version,
        )


def _sandbox_flag(request: AgentRequest) -> str:
    if request.permission_profile in ("discussion", "read_only"):
        return "read-only"
    return "workspace-write"


def _validate_profile(request: AgentRequest, *, write_verified: bool) -> None:
    if request.permission_profile == "sandboxed_write" and not write_verified:
        raise RequestPolicyError(
            "sandboxed_write rejected: Codex sandbox canary has not been "
            "verified on this machine (fail closed, §27.1)"
        )
    if not request.prompt.strip():
        raise RequestPolicyError("empty prompts are rejected (fail closed)")


def find_executable(executable: str) -> bool:
    """Executable existence probe without launching a process."""
    if os.path.isabs(executable) or "/" in executable or "\\" in executable:
        return os.path.isfile(executable)
    return bool(
        shutil.which(executable)
        or shutil.which(f"{executable}.exe")
        or shutil.which(f"{executable}.cmd")
    )
