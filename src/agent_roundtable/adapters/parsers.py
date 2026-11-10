"""Provider output parsers (§27.5, §27.6).

Codex JSONL parsing is written ONLY against the event types captured on the
installed CLI (docs/milestone0/codex_jsonl_fixture_redacted.txt):
``thread.started``, ``turn.started``, ``item.started``/``item.completed`` with
``item.type`` in {``agent_message``, ``command_execution``}, ``turn.completed``.

Fail-closed contract (captured 2026-11-01):
- final response = text of the LAST ``item.completed`` whose item.type == "agent_message"
- provider session id = ``thread.started.thread_id``; usage = ``turn.completed.usage``
- unknown event types -> non-fatal diagnostics
- a malformed JSON line OR a missing agent message -> status unknown/error, NEVER success
- plain ``turn.completed`` is not a successful run

Antigravity returns a JSON envelope; non-success provider status is never
success even with exit code zero, and a tool soft-denial while "success" is
reported as ``blocked``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

CODEX_PARSER_VERSION = "codex-parser-1.0.0-fixture-2026-11-01"
ANTIGRAVITY_PARSER_VERSION = "antigravity-parser-1.0.0-docs-2026-11-01"

#: stderr / diagnostic markers that indicate a permission soft-denial for
#: Antigravity headless runs (documented headless behavior, §27.6).
_ANTIGRAVITY_DENIAL_MARKERS: tuple[str, ...] = (
    "permission denied",
    "requires approval",
    "requires permission",
    "not permitted",
    "denied by policy",
    "denied",
)

_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "authentication expired",
    "authentication required",
    "not authenticated",
    "not logged in",
    "please log in",
    "unauthorized",
)
_QUOTA_FAILURE_MARKERS: tuple[str, ...] = (
    "quota exceeded",
    "usage limit reached",
    "rate limit exceeded",
    "too many requests",
    "http 429",
    "status 429",
)


def classify_provider_pause(*blobs: str | None) -> str | None:
    """Classify explicit CLI diagnostics that require operator intervention.

    Only provider error channels are passed here (stderr or structured error
    fields), never agent response text. Unknown errors stay ordinary failures.
    """
    text = "\n".join(blob for blob in blobs if blob).lower()
    if any(marker in text for marker in _AUTH_FAILURE_MARKERS):
        return "authentication_required"
    if any(marker in text for marker in _QUOTA_FAILURE_MARKERS):
        return "quota_exhausted"
    return None


@dataclass
class CodexParseResult:
    response_text: str | None = None
    provider_session_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    provider_status: str | None = None
    diagnostics: list[str] = field(default_factory=list)
    saw_agent_message: bool = False
    had_malformed_line: bool = False
    had_exit_event: bool = False
    turn_completed: bool = False


def parse_codex_events(raw_text: str | None) -> CodexParseResult:
    """Strict, fail-closed JSONL parse per the captured fixture contract."""
    parsed = CodexParseResult()
    if raw_text is None or not raw_text.strip():
        parsed.diagnostics.append("no JSONL output captured")
        return parsed
    for index, line in enumerate(raw_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            parsed.had_malformed_line = True
            parsed.diagnostics.append(f"line {index}: malformed JSON (ignored content)")
            continue
        if not isinstance(event, dict) or "type" not in event:
            parsed.had_malformed_line = True
            parsed.diagnostics.append(f"line {index}: non-object or typeless event")
            continue
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str):
                parsed.provider_session_id = thread_id
        elif event_type == "turn.started":
            pass
        elif event_type in ("item.started", "item.completed"):
            item = event.get("item")
            if not isinstance(item, dict) or "type" not in item:
                parsed.had_malformed_line = True
                parsed.diagnostics.append(f"line {index}: item event without item.type")
                continue
            item_type = item.get("type")
            if item_type == "agent_message" and event_type == "item.completed":
                text = item.get("text")
                if isinstance(text, str):
                    parsed.response_text = text  # LAST agent_message wins
                    parsed.saw_agent_message = True
            elif item_type == "command_execution":
                # progress events stay as richer-on-demand diagnostics; only
                # failed command executions are surfaced by name
                status = item.get("status")
                if event_type == "item.completed" and status not in (None, "completed"):
                    parsed.diagnostics.append(
                        f"line {index}: command_execution finished with status={status!r}"
                    )
            else:
                parsed.diagnostics.append(
                    f"line {index}: unrecognized item.type={item_type!r} "
                    "(non-fatal per §27.5)"
                )
        elif event_type == "turn.completed":
            parsed.turn_completed = True
            usage = event.get("usage")
            if isinstance(usage, dict):
                parsed.usage = usage
        else:
            parsed.diagnostics.append(
                f"line {index}: unrecognized event type={event_type!r} "
                "(non-fatal per §27.5)"
            )
    return parsed


def codex_status(
    parse_result: CodexParseResult,
    *,
    exit_code: int | None,
    timed_out: bool,
    canceled: bool,
) -> tuple[str, str | None]:
    """Derive (AgentRunStatus, reason) — fail closed, never assume success."""
    if timed_out:
        return "timed_out", None
    if canceled:
        return "canceled", None
    if exit_code != 0:
        # includes failed_to_start (exit_code None)
        return "error", "non_zero_exit"
    reasons: list[str] = []
    if parse_result.had_malformed_line:
        reasons.append("malformed_event_stream")
        return "unknown", " + ".join(reasons)
    if exit_code == 0 and not parse_result.saw_agent_message:
        return "unknown", "missing_final_message"
    if parse_result.saw_agent_message:
        return "success", None
    return "unknown", "missing_final_message"


@dataclass
class AntigravityParseResult:
    response_text: str | None = None
    provider_session_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    provider_status: str | None = None
    error_text: str | None = None
    diagnostics: list[str] = field(default_factory=list)
    soft_denied: bool = False
    had_malformed_payload: bool = False


def detect_antigravity_denial(*blobs: str | None) -> bool:
    for blob in blobs:
        if not blob:
            continue
        lowered = blob.lower()
        if any(marker in lowered for marker in _ANTIGRAVITY_DENIAL_MARKERS):
            return True
    return False


def parse_antigravity_envelope(
    raw_text: str | None, *, stderr_text: str | None = None
) -> AntigravityParseResult:
    """Parse the documented response envelope. Malformed -> fail closed."""
    parsed = AntigravityParseResult()
    if raw_text is None or not raw_text.strip():
        parsed.had_malformed_payload = True
        parsed.diagnostics.append("no JSON envelope captured")
        return parsed
    try:
        envelope = json.loads(raw_text.strip())
    except (json.JSONDecodeError, ValueError):
        envelope = None
        for line in raw_text.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            try:
                item = json.loads(line_str)
                if (
                    isinstance(item, dict)
                    and item.get("event") == "result"
                    and isinstance(item.get("result"), dict)
                ):
                    envelope = item["result"]
                    break
            except (json.JSONDecodeError, ValueError):
                continue
        if envelope is None:
            parsed.had_malformed_payload = True
            parsed.diagnostics.append("malformed JSON envelope")
            return parsed
    if not isinstance(envelope, dict):
        parsed.had_malformed_payload = True
        parsed.diagnostics.append("envelope is not a JSON object")
        return parsed
    status = envelope.get("status")
    parsed.provider_status = str(status) if status is not None else None
    response = envelope.get("response")
    if isinstance(response, str):
        parsed.response_text = response
    error = envelope.get("error")
    if error is not None:
        parsed.error_text = json.dumps(error) if not isinstance(error, str) else error
    conversation_id = envelope.get("conversation_id")
    if isinstance(conversation_id, str):
        parsed.provider_session_id = conversation_id
    usage = envelope.get("usage")
    if isinstance(usage, dict):
        parsed.usage = usage
    diagnostics = envelope.get("diagnostics")
    if isinstance(diagnostics, list):
        parsed.diagnostics.extend(str(d) for d in diagnostics)
    # §27.6: denial notices come from stderr and structured output — the agent's
    # own response text is deliberately NOT scanned (it may legitimately
    # discuss a denial without actually being denied).
    blob_sources = list(parsed.diagnostics) + [parsed.error_text, stderr_text]
    parsed.soft_denied = detect_antigravity_denial(*blob_sources)
    return parsed


def antigravity_status(
    parse_result: AntigravityParseResult,
    *,
    exit_code: int | None,
    timed_out: bool,
    canceled: bool,
) -> tuple[str, str | None]:
    """exit code zero does NOT mean success (§27.6) — status comes from the envelope."""
    if timed_out:
        return "timed_out", None
    if canceled:
        return "canceled", None
    if exit_code != 0:
        return "error", "non_zero_exit"
    if parse_result.had_malformed_payload:
        return "unknown", "malformed_payload"
    if parse_result.soft_denied:
        return "blocked", "tool_soft_denied"
    status = (
        parse_result.provider_status.lower()
        if isinstance(parse_result.provider_status, str)
        else None
    )
    if status == "success":
        if parse_result.response_text is None:
            return "unknown", "missing_response_field"
        return "success", None
    if status is None or not isinstance(status, str):
        return "unknown", "missing_provider_status"
    # any documented non-success status stays itself-based, but never success
    return "error", f"provider_status_{status}"
