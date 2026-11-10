from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from agent_roundtable.adapters.antigravity import AntigravityAdapter
from agent_roundtable.adapters.codex import CodexAdapter
from agent_roundtable.adapters.parsers import (
    antigravity_status,
    classify_provider_pause,
    codex_status,
    parse_antigravity_envelope,
    parse_codex_events,
)
from agent_roundtable.domain.models import (
    AdapterNotAvailableError,
    AgentRequest,
    AgentRunStatus,
    PromptTooLongError,
    RequestPolicyError,
    RunPurpose,
)
from agent_roundtable.execution.supervisor import SupervisedResult, Supervisor


def test_codex_parser_extracts_last_message_session_and_usage() -> None:
    raw = "\n".join(
        (
            json.dumps({"type": "thread.started", "thread_id": "session-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "first"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "last"},
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 7}}),
        )
    )
    parsed = parse_codex_events(raw)
    assert parsed.response_text == "last"
    assert parsed.provider_session_id == "session-1"
    assert parsed.usage == {"input_tokens": 7}
    assert codex_status(parsed, exit_code=0, timed_out=False, canceled=False) == (
        "success",
        None,
    )


@pytest.mark.parametrize(
    ("raw", "exit_code", "expected", "reason"),
    [
        ('{"type":"turn.completed"}', 0, "unknown", "missing_final_message"),
        ("not json", 0, "unknown", "malformed_event_stream"),
        (
            '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}',
            1,
            "error",
            "non_zero_exit",
        ),
    ],
)
def test_codex_parser_fails_closed(
    raw: str, exit_code: int, expected: str, reason: str
) -> None:
    parsed = parse_codex_events(raw)
    status, actual_reason = codex_status(
        parsed, exit_code=exit_code, timed_out=False, canceled=False
    )
    assert (status, actual_reason) == (expected, reason)


def test_antigravity_status_respects_envelope_denial_and_exit_code() -> None:
    success = parse_antigravity_envelope(
        json.dumps({"status": "success", "response": "answer"})
    )
    assert antigravity_status(
        success, exit_code=0, timed_out=False, canceled=False
    ) == ("success", None)

    # Test NDJSON stream-json parsing
    ndjson_lines = (
        '{"event":"step_update","step_update":{"text_delta":"streaming..."}}\n'
        '{"event":"result","result":{"status":"SUCCESS","response":"streamed answer"}}\n'
    )
    stream_success = parse_antigravity_envelope(ndjson_lines)
    assert stream_success.response_text == "streamed answer"
    assert antigravity_status(
        stream_success, exit_code=0, timed_out=False, canceled=False
    ) == ("success", None)

    denied = parse_antigravity_envelope(
        json.dumps(
            {
                "status": "success",
                "response": "partial",
                "diagnostics": ["write_file not permitted without approval"],
            }
        )
    )
    assert antigravity_status(denied, exit_code=0, timed_out=False, canceled=False) == (
        "blocked",
        "tool_soft_denied",
    )
    assert antigravity_status(
        success, exit_code=9, timed_out=False, canceled=False
    ) == ("error", "non_zero_exit")


def test_provider_pause_classifier_uses_explicit_error_text_only() -> None:
    assert classify_provider_pause("HTTP 429: too many requests") == "quota_exhausted"
    assert (
        classify_provider_pause("authentication expired") == "authentication_required"
    )
    assert classify_provider_pause("response discusses a rate limit") is None


class _CapturingSupervisor:
    def __init__(self, raw: SupervisedResult) -> None:
        self.raw = raw
        self.command: list[str] | None = None
        self.kwargs: dict[str, object] = {}

    async def run(self, command: list[str], **kwargs: object) -> SupervisedResult:
        self.command = command
        self.kwargs = kwargs
        return self.raw

    async def cancel(self, run_id: object) -> bool:
        return True


def _request(
    provider: str = "codex",
    *,
    purpose: RunPurpose = RunPurpose.DIAGNOSTIC,
    profile: str = "read_only",
    prompt: str = "hello",
) -> AgentRequest:
    return AgentRequest(
        agent_id=provider,
        purpose=purpose,
        permission_profile=profile,
        prompt=prompt,
    )


@pytest.mark.asyncio
async def test_codex_adapter_builds_safe_command_and_stdin_request() -> None:
    raw = SupervisedResult(
        exit_code=0,
        stdout='{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}',
        started_at="start",
        finished_at="finish",
    )
    supervisor = _CapturingSupervisor(raw)
    adapter = CodexAdapter(supervisor=supervisor, executable="codex.exe")  # type: ignore[arg-type]
    result = await adapter.run(_request())
    assert result.status is AgentRunStatus.SUCCESS
    assert result.response_text == "ok"
    assert supervisor.command is not None
    assert supervisor.command[:3] == ["codex.exe", "--no-daemon", "exec"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in supervisor.command
    assert supervisor.kwargs["stdin_data"] == b"hello"


@pytest.mark.asyncio
async def test_adapters_fail_closed_on_unverified_writes_and_missing_agy() -> None:
    adapter = CodexAdapter(supervisor=_CapturingSupervisor(SupervisedResult()))  # type: ignore[arg-type]
    implementation = AgentRequest(
        agent_id="codex",
        purpose=RunPurpose.IMPLEMENTATION,
        task_id=uuid4(),
        project_id=uuid4(),
        working_directory=str(Path.cwd()),
        permission_profile="sandboxed_write",
        prompt="change a file",
    )
    with pytest.raises(RequestPolicyError):
        await adapter.run(implementation)

    agy = AntigravityAdapter(supervisor=_CapturingSupervisor(SupervisedResult()))  # type: ignore[arg-type]
    with pytest.raises(AdapterNotAvailableError):
        await agy.run(_request("antigravity"))


@pytest.mark.asyncio
async def test_antigravity_adapter_rejects_overlong_prompts_before_launch() -> None:
    supervisor = _CapturingSupervisor(SupervisedResult())
    adapter = AntigravityAdapter(
        supervisor=supervisor,
        available=True,
        sandbox_verified=True,  # type: ignore[arg-type]
    )
    with pytest.raises(PromptTooLongError):
        await adapter.run(_request("antigravity", prompt="x" * 8001))
    assert supervisor.command is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("success", AgentRunStatus.SUCCESS),
        ("zero_exit_no_final", AgentRunStatus.UNKNOWN),
        ("malformed_json_line", AgentRunStatus.UNKNOWN),
        ("failure_exit_nonzero", AgentRunStatus.ERROR),
        ("quota_exhausted", AgentRunStatus.BLOCKED),
        ("auth_expired", AgentRunStatus.BLOCKED),
    ],
)
async def test_codex_fake_cli_contract(scenario: str, expected: AgentRunStatus) -> None:
    fixture = (
        Path(__file__).resolve().parents[1] / "fixtures" / "fake_cli" / "fake_codex.py"
    )
    adapter = CodexAdapter(
        supervisor=Supervisor(),
        executable="fake-codex",
        wrapper=[sys.executable, str(fixture)],
        env_overrides={"AGENTROUNDTABLE_FAKE_SCENARIO": scenario},
        test_mode=True,
    )
    result = await adapter.run(_request())
    assert result.status is expected
    if scenario == "success":
        assert result.response_text == "FAKE_CODEX_OK"
        assert result.usage["input_tokens"] == 100
    if scenario in {"quota_exhausted", "auth_expired"}:
        assert result.reason_code == (
            "quota_exhausted"
            if scenario == "quota_exhausted"
            else "authentication_required"
        )
        assert (await adapter.health_check()).state == "DEGRADED"
        assert adapter.operational_status() == (
            "QUOTA_BLOCKED" if scenario == "quota_exhausted" else "AUTH_EXPIRED"
        )
        with pytest.raises(AdapterNotAvailableError):
            await adapter.run(_request())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("success", AgentRunStatus.SUCCESS),
        ("soft_denial", AgentRunStatus.BLOCKED),
        ("status_non_success_exit_zero", AgentRunStatus.ERROR),
        ("malformed_json", AgentRunStatus.UNKNOWN),
        ("auth_expired", AgentRunStatus.BLOCKED),
    ],
)
async def test_antigravity_fake_cli_contract(
    scenario: str, expected: AgentRunStatus
) -> None:
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "fake_cli"
        / "fake_antigravity.py"
    )
    adapter = AntigravityAdapter(
        supervisor=Supervisor(),
        executable="fake-agy",
        wrapper=[sys.executable, str(fixture)],
        available=True,
        sandbox_verified=True,
        env_overrides={"AGENTROUNDTABLE_FAKE_SCENARIO": scenario},
        test_mode=True,
    )
    result = await adapter.run(_request("antigravity"))
    assert result.status is expected
    if scenario == "success":
        assert result.response_text == "FAKE_ANTIGRAVITY_OK"
        assert result.provider_session_id == "FAKE-CONV-1"
    if scenario == "auth_expired":
        assert result.reason_code == "authentication_required"
        assert (await adapter.health_check()).state == "DEGRADED"
        with pytest.raises(AdapterNotAvailableError):
            await adapter.run(_request("antigravity"))


@pytest.mark.asyncio
async def test_truncated_provider_output_never_maps_to_success() -> None:
    raw = SupervisedResult(
        exit_code=0,
        stdout='{"type":"item.completed","item":{"type":"agent_message","text":"answer"}}',
        stdout_truncated=True,
    )
    codex = CodexAdapter(supervisor=_CapturingSupervisor(raw))  # type: ignore[arg-type]
    antigravity = AntigravityAdapter(
        supervisor=_CapturingSupervisor(raw),
        available=True,
        sandbox_verified=True,
    )  # type: ignore[arg-type]
    assert (await codex.run(_request())).status is AgentRunStatus.UNKNOWN
    assert (
        await antigravity.run(_request("antigravity"))
    ).status is AgentRunStatus.UNKNOWN


@pytest.mark.asyncio
async def test_domain_rejects_discussion_with_project_or_worktree() -> None:
    with pytest.raises(ValueError):
        AgentRequest(
            agent_id="codex",
            purpose=RunPurpose.DISCUSSION,
            project_id=uuid4(),
            working_directory=str(Path.cwd()),
            permission_profile="discussion",
            prompt="hello",
        )
