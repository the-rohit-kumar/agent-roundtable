"""Antigravity adapter (§27.6) — fail-closed by design (DECISIONS D5).

Until `agy` is installed AND a positive filesystem/command sandbox canary has
passed on this exact machine/version, the adapter is `available=False` and
every run() attempt is refused with `AdapterNotAvailableError`. Health always
reports DISABLED / (NOT_INSTALLED | SANDBOX_UNVERIFIED) — demonstrated by
live evidence, never assumed from the docs.

Command shape (prompt as CLI argument; conservative length cap §27.8)::

    agy -p <prompt> --output-format json

`--dangerously-skip-permissions` is prohibited. `--mode=auto` is NOT treated
as proof of a sandbox: fallbacks are detected and fail closed.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from uuid import UUID

from agent_roundtable.adapters import env as env_allowlist
from agent_roundtable.adapters.parsers import (
    ANTIGRAVITY_PARSER_VERSION,
    antigravity_status,
    classify_provider_pause,
    parse_antigravity_envelope,
)
from agent_roundtable.domain.models import (
    AdapterNotAvailableError,
    AgentCapabilities,
    AgentHealth,
    AgentRequest,
    AgentRunResult,
    AgentRunStatus,
    PromptTooLongError,
    RequestPolicyError,
)
from agent_roundtable.execution.supervisor import SupervisedResult, Supervisor
from agent_roundtable.observability.logging import redact

logger = logging.getLogger("agent_roundtable.adapters.antigravity")

#: Conservative Windows command-line cap (§27.8). The documented headless form
#: passes the prompt as an argv value; 8000 chars leaves ample room.
_MAX_PROMPT_CHARS = 8000


class AntigravityAdapter:
    agent_id = "antigravity"

    def __init__(
        self,
        *,
        supervisor: Supervisor,
        executable: str = "agy",
        available: bool = False,  # fail closed until provider canary passes (D5)
        sandbox_verified: bool = False,
        env_overrides: dict[str, str] | None = None,
        wrapper: list[str] | None = None,  # test-only injection point
        test_mode: bool = False,
    ) -> None:
        self._supervisor = supervisor
        self._executable = executable
        self._available = available
        self._sandbox_verified = sandbox_verified
        self._env_overrides = env_overrides or {}
        self._wrapper = list(wrapper or [])
        self._test_mode = test_mode
        self._paused_reason: str | None = None

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            agent_id=self.agent_id,
            read_only_supported=self._available,
            sandboxed_write_supported=False,  # stays False until a positive canary
            stateful_resume_supported=False,  # conversation_id stored; resume optional (D9)
            structured_output_supported=True,
            notes="headless -p/--output-format json; soft-denial => blocked; fail closed (D5)",
        )

    def operational_status(self) -> str:
        """Report local state without starting a provider request."""
        if self._paused_reason == "quota_exhausted":
            return "QUOTA_BLOCKED"
        if self._paused_reason:
            return "AUTH_EXPIRED"
        from agent_roundtable.adapters.codex import find_executable

        if not find_executable(self._executable):
            return "CLI_MISSING"
        return "CLI_AVAILABLE" if self._available else "SANDBOX_UNVERIFIED"

    async def health_check(self) -> AgentHealth:
        from agent_roundtable.adapters.codex import (
            find_executable as _find,  # local reuse
        )

        if self._paused_reason is not None:
            return AgentHealth(
                agent_id=self.agent_id,
                state="DEGRADED",
                reason_code=self._paused_reason.upper(),
                detail="provider paused pending interactive recovery",
            )
        if not self._available:
            found = _find(self._executable)
            reason = "SANDBOX_UNVERIFIED" if found else "NOT_INSTALLED"
            return AgentHealth(
                agent_id=self.agent_id,
                state="DISABLED",
                reason_code=reason,
                detail=(
                    "'agy' found but sandbox canary not passed on this machine — "
                    "agent calls stay disabled (fail closed, D5)"
                    if found
                    else "Antigravity CLI ('agy') not found on PATH"
                ),
            )
        request = AgentRequest(
            agent_id="antigravity",
            purpose="diagnostic",
            permission_profile="read_only",
            prompt="harness healthcheck: reply with exactly ROUNDTABLE_HEALTHCHECK_OK",
            timeout_seconds=90,
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
                "antigravity adapter is DISABLED until 'agy' is installed and "
                "the sandbox canary passes (fail closed, DECISIONS D5)"
            )
        if self._paused_reason is not None:
            raise AdapterNotAvailableError(
                f"antigravity provider paused ({self._paused_reason}); interactive recovery required"
            )
        if request.prompt is None or not request.prompt.strip():
            raise RequestPolicyError("empty prompts are rejected (fail closed)")
        if len(request.prompt) > _MAX_PROMPT_CHARS:
            raise PromptTooLongError(
                f"prompt of {len(request.prompt)} chars exceeds the documented "
                f"command-line-safe cap of {_MAX_PROMPT_CHARS} (§27.8); assemble "
                "a smaller context instead of relying on the adapter to truncate"
            )
        _validate_profile(request, sandbox_verified=self._sandbox_verified)
        output_format = "stream-json" if on_stream_delta is not None else "json"
        command = [
            *self._wrapper,
            self._executable,
            "-p",
            request.prompt,
            "--output-format",
            output_format,
        ]
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
                data = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                return
            if not isinstance(data, dict):
                return
            step_update = data.get("step_update")
            if isinstance(step_update, dict):
                delta = step_update.get("text_delta")
                if isinstance(delta, str) and delta:
                    await on_stream_delta(delta)

        raw = await self._supervisor.run(
            command,
            run_id=request.run_id,
            agent_id=request.agent_id,
            env=child_env,
            cwd=request.working_directory,
            timeout_seconds=float(request.timeout_seconds),
            max_stdout_bytes=request.max_stdout_bytes,
            max_stderr_bytes=request.max_stderr_bytes,
            on_stdout_line=_on_line if on_stream_delta is not None else None,
        )
        return self._map_result(request, raw, started)

    async def cancel(self, run_id: UUID) -> bool:
        return await self._supervisor.cancel(run_id)

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
        parse = parse_antigravity_envelope(raw.stdout, stderr_text=raw.stderr)
        pause_reason = classify_provider_pause(parse.error_text, raw.stderr)
        status_text, reason_text = antigravity_status(
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
        # exit zero with soft-denial must still surface 'blocked' (§27.6) —
        # antigravity_status already handles that ordering.
        diagnostics = list(parse.diagnostics)
        if parse.error_text:
            diagnostics.append(f"provider error: {parse.error_text}")
        diagnostics.extend(line for line in raw.stderr.splitlines() if line.strip())
        return self._finish(
            request,
            raw,
            started,
            status=AgentRunStatus(status_text),
            response_text=parse.response_text if status_text == "success" else None,
            provider_session_id=parse.provider_session_id,
            usage=parse.usage,
            diagnostics=[redact(line) for line in diagnostics],
            reason_code=reason_text,
            provider_status=parse.provider_status,
            parser_version=ANTIGRAVITY_PARSER_VERSION,
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
        provider_status: str | None = None,
        parser_version: str = "antigravity-adapter-internal",
    ) -> AgentRunResult:
        return AgentRunResult(
            run_id=request.run_id,
            agent_id=request.agent_id,
            status=status,
            response_text=response_text,
            provider_session_id=provider_session_id,
            provider_status=provider_status,
            exit_code=raw.exit_code,
            started_at=raw.started_at,
            finished_at=raw.finished_at,
            duration_ms=int((time.monotonic() - started) * 1000) or raw.duration_ms,
            usage=dict(usage or {}),
            diagnostics=list(diagnostics or []),
            reason_code=reason_code,
            parser_version=parser_version,
        )


def _validate_profile(request: AgentRequest, *, sandbox_verified: bool) -> None:
    if request.permission_profile == "sandboxed_write" and not sandbox_verified:
        raise RequestPolicyError(
            "sandboxed_write rejected: Antigravity sandbox canary not passed "
            "(fail closed, §27.6)"
        )
