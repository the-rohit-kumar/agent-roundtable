"""Validate, deduplicate and dispatch untrusted event payloads."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_roundtable.domain.models import EventCreate, EventType
from agent_roundtable.persistence.repositories.events import EventRepository

logger = logging.getLogger("agent_roundtable.events")


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=4000)
    title: str | None = Field(default=None, max_length=200)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)
    parent_event_id: str | None = None
    chain_depth: int = Field(default=0, ge=0, le=3)
    actor_id: str | None = Field(default=None, max_length=64)


Dispatch = Callable[[dict[str, Any]], Awaitable[None]]


class EventRouter:
    def __init__(
        self, repository: EventRepository, duplicate_window_minutes: int = 30
    ) -> None:
        self.repository = repository
        self.duplicate_window_minutes = max(1, duplicate_window_minutes)

    async def ingest(
        self,
        *,
        event_type: EventType,
        source: str,
        summary: str,
        visibility: str,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
        title: str | None = None,
        evidence_refs: list[str] | None = None,
        parent_event_id: str | None = None,
        chain_depth: int = 0,
        actor_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if source not in {
            "discord",
            "git",
            "test_runner",
            "agent",
            "scheduler",
            "controller",
        }:
            raise ValueError("unsupported event source")
        if event_type in {EventType.AGENT_FOLLOW_UP, EventType.AGENT_DISCOVERY} and (
            not evidence_refs or chain_depth >= 3
        ):
            raise ValueError(
                "agent events require new evidence and a chain depth below 3"
            )
        if event_type is EventType.AGENT_DISCOVERY and source != "agent":
            raise ValueError("agent discoveries must originate from an agent")
        if event_type is EventType.AGENT_DISCOVERY and not evidence_refs:
            raise ValueError("agent discovery requires evidence references")
        payload = EventPayload(
            summary=summary,
            title=title,
            evidence_refs=evidence_refs or [],
            parent_event_id=parent_event_id,
            chain_depth=chain_depth,
            actor_id=actor_id,
        )
        canonical = json.dumps(
            {
                "source": source,
                "project_id": str(project_id) if project_id else None,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "payload": payload.model_dump(mode="json"),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        # Time-bucketed fingerprints permit a genuinely new event after the
        # configured suppression window while remaining stable within it.
        bucket = math.floor(
            datetime.now(UTC).timestamp() / (self.duplicate_window_minutes * 60)
        )
        dedupe_key = f"{fingerprint}:{bucket}"
        event = EventCreate(
            event_type=event_type,
            source=source,
            project_id=project_id,
            conversation_id=conversation_id,
            dedupe_key=dedupe_key,
            visibility=visibility,
            payload_json=payload.model_dump_json(),
        )
        return await self.repository.enqueue_if_new(event)

    async def dispatch_next(self, handler: Dispatch) -> bool:
        event = await self.repository.claim_next()
        if event is None:
            return False
        await self._dispatch(event, handler)
        return True

    async def dispatch_event(self, event_id: str, handler: Dispatch) -> bool:
        """Claim a specific just-enqueued event (used by acknowledged commands)."""
        event = await self.repository.get(event_id)
        if event is None or event["status"] != "pending":
            return False

        def claim(conn):
            cursor = conn.execute(
                "UPDATE events SET status='processing',attempt_count=attempt_count+1 "
                "WHERE event_id=? AND status='pending'",
                (event_id,),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT * FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            return dict(row)

        claimed = await self.repository.db.run_transaction(claim)
        if claimed is None:
            return False
        await self._dispatch(claimed, handler)
        return True

    async def _dispatch(self, event: dict[str, Any], handler: Dispatch) -> None:
        try:
            event_type = EventType(event["event_type"])
            payload = EventPayload.model_validate_json(event["payload_json"])
        except (ValueError, ValidationError, TypeError):
            await self.repository.reject(event["event_id"], "invalid_event_payload")
            return True
        try:
            await handler({**event, "event_type": event_type, "payload": payload})
        except Exception as exc:  # noqa: BLE001 - event boundary terminally records failures
            reason = str(getattr(exc, "reason_code", "dispatcher_error"))
            await self.repository.fail(event["event_id"], reason, retryable=False)
            logger.warning(
                "event dispatch failed",
                extra={"event_id": event["event_id"], "error_type": type(exc).__name__},
            )
        else:
            await self.repository.complete(event["event_id"])

    async def drain(self, handler: Dispatch, *, max_events: int = 32) -> int:
        processed = 0
        for _ in range(max_events):
            if not await self.dispatch_next(handler):
                break
            processed += 1
        return processed
