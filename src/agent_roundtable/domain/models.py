"""Shared domain models (ARCHITECTURE §26.4).

Concrete Pydantic models used by repositories and services. Kept free of any
infrastructure imports so the domain layer stays serializable and testable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def new_id() -> UUID:
    return uuid4()


def utc_now_iso() -> str:
    """UTC ISO-8601 timestamp (ARCHITECTURE §25: store UTC, display local tz)."""
    from agent_roundtable.util import utc_now_iso as _util_now

    return _util_now()


class AgentProvider(StrEnum):
    CODEX = "codex"
    ANTIGRAVITY = "antigravity"


class _Domain(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AgentSeed(_Domain):
    agent_id: str
    provider: AgentProvider
    display_name: str
    persona_path: str
    enabled: bool = True


class ConversationMode(StrEnum):
    OPEN_ROOM = "open_room"
    DEBATE = "debate"
    RESEARCH = "research"
    BRAINSTORM = "brainstorm"
    CODE_REVIEW = "code_review"
    DECISION = "decision"


class ConversationVisibility(StrEnum):
    SHARED = "shared"
    OWNER_ONLY = "owner_only"


class ConversationOrigin(StrEnum):
    OWNER = "owner"
    FRIEND = "friend"
    AGENT = "agent"
    EVENT_SOURCE = "event_source"
    SCHEDULE = "schedule"
    SYSTEM = "system"


class ConversationStatus(StrEnum):
    OPEN = "open"
    WAITING_FOR_AGENT = "waiting_for_agent"
    WAITING_FOR_USER = "waiting_for_user"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class ConversationCreate(_Domain):
    conversation_id: UUID = Field(default_factory=new_id)
    project_id: UUID | None = None
    mode: ConversationMode = ConversationMode.OPEN_ROOM
    visibility: ConversationVisibility = ConversationVisibility.OWNER_ONLY
    origin: ConversationOrigin
    status: ConversationStatus = ConversationStatus.OPEN
    discord_channel_id: str | None = None
    discord_thread_id: str | None = None
    created_by_discord_id: str | None = None
    trigger_event_id: UUID | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    closed_at: str | None = None


class AuthorType(StrEnum):
    AGENT = "agent"
    OWNER = "owner"
    FRIEND = "friend"
    SYSTEM = "system"


class MessageCreate(_Domain):
    message_id: UUID = Field(default_factory=new_id)
    conversation_id: UUID
    author_type: AuthorType
    author_id: str
    content: str
    visibility: ConversationVisibility
    discord_message_id: str | None = None
    provider_run_id: UUID | None = None
    reply_to_message_id: UUID | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    metadata_json: str = "{}"


class EventType(StrEnum):
    DISCORD_COMMAND = "discord.command"
    GIT_COMMIT = "git.commit"
    GIT_CHANGED_PATHS = "git.changed_paths"
    TEST_FAILED = "test.failed"
    TEST_COMPLETED = "test.completed"
    TASK_COMPLETED = "task.completed"
    AGENT_DISCOVERY = "agent.discovery"
    AGENT_FOLLOW_UP = "agent.follow_up"
    SCHEDULE_TICK = "schedule.tick"
    CONTROLLER_RECOVERED = "controller.recovered"


class EventStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class EventCreate(_Domain):
    event_id: UUID = Field(default_factory=new_id)
    event_type: EventType
    source: Literal["discord", "git", "test_runner", "agent", "scheduler", "controller"]
    project_id: UUID | None = None
    conversation_id: UUID | None = None
    dedupe_key: str
    visibility: ConversationVisibility
    payload_json: str
    status: EventStatus = EventStatus.PENDING
    attempt_count: int = 0
    created_at: str = Field(default_factory=utc_now_iso)
    available_at: str = Field(default_factory=utc_now_iso)
    processed_at: str | None = None
    failure_code: str | None = None


class AuditResult(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"
    BLOCKED = "blocked"


class AuditEventCreate(_Domain):
    audit_id: UUID = Field(default_factory=new_id)
    actor_type: Literal["owner", "friend", "agent", "controller", "system", "cli"]
    actor_id: str
    action: str
    target_type: str | None = None
    target_id: str | None = None
    result: AuditResult = AuditResult.SUCCESS
    reason_code: str | None = None
    correlation_id: str
    created_at: str = Field(default_factory=utc_now_iso)
    details_json: str = "{}"


class AgentRunStatus(StrEnum):
    # First-class result statuses (§27.2). "blocked" covers provider soft-denials.
    SUCCESS = "success"
    ERROR = "error"
    BLOCKED = "blocked"
    CANCELED = "canceled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class RunPurpose(StrEnum):
    DISCUSSION = "discussion"
    RESEARCH = "research"
    REVIEW = "review"
    IMPLEMENTATION = "implementation"
    DIAGNOSTIC = "diagnostic"


class AgentSessionRecord(_Domain):
    """Provider session continuity is optional by design (DECISIONS D9)."""

    agent_session_id: UUID = Field(default_factory=new_id)
    agent_id: str
    provider_session_id: str
    conversation_id: UUID | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class AgentCapabilities(_Domain):
    agent_id: str
    read_only_supported: bool = False
    sandboxed_write_supported: bool = False
    stateful_resume_supported: bool = False
    structured_output_supported: bool = False
    notes: str | None = None
