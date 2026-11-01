"""Agent identity + run-status domain invariants (§27.1, §27.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_roundtable.domain.models import (
    AgentRunStatus,
    AuditResult,
    ConversationCreate,
    ConversationOrigin,
    ConversationVisibility,
    EventCreate,
    EventType,
    RunPurpose,
)


def test_run_status_includes_blocked_first_class() -> None:
    # §27.2: "blocked" is a first-class status, especially for provider soft-denials
    assert AgentRunStatus.BLOCKED.value == "blocked"
    assert AgentRunStatus.UNKNOWN.value == "unknown"


def test_purpose_enum_matches_spec() -> None:
    assert {p.value for p in RunPurpose} == {
        "discussion",
        "research",
        "review",
        "implementation",
        "diagnostic",
    }


def test_event_types_match_dispatch_contract() -> None:
    expected = {
        "discord.command",
        "git.commit",
        "git.changed_paths",
        "test.failed",
        "test.completed",
        "task.completed",
        "agent.discovery",
        "agent.follow_up",
        "schedule.tick",
        "controller.recovered",
    }
    assert {e.value for e in EventType} == expected


def test_friend_conversation_defaults_no_project() -> None:
    conv = ConversationCreate(
        origin=ConversationOrigin.FRIEND, visibility=ConversationVisibility.SHARED
    )
    assert conv.project_id is None  # §25.3: friend conversations never carry a project
    assert conv.mode.value == "open_room"


def test_event_create_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError):
        EventCreate(
            event_type=EventType.DISCORD_COMMAND,
            source="from_the_internet",  # not in the allowed source set
            dedupe_key="k",
            visibility=ConversationVisibility.OWNER_ONLY,
            payload_json="{}",
        )


def test_audit_results_denied() -> None:
    assert AuditResult.DENIED.value == "denied"
