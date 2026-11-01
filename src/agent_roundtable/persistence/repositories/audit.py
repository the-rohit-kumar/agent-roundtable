"""Audit event repository (append-only in normal operation, §29.2)."""

from __future__ import annotations

from typing import Any

from agent_roundtable.domain.models import AuditEventCreate
from agent_roundtable.persistence.database import Database


class AuditRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(self, event: AuditEventCreate) -> None:
        await self._db.execute(
            """
            INSERT INTO audit_events (
                audit_id, actor_type, actor_id, action, target_type, target_id,
                result, reason_code, correlation_id, created_at, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(event.audit_id),
                event.actor_type,
                event.actor_id,
                event.action,
                event.target_type,
                event.target_id,
                event.result.value,
                event.reason_code,
                event.correlation_id,
                event.created_at,
                event.details_json,
            ),
        )

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self._db.fetch_all(
            "SELECT * FROM audit_events ORDER BY created_at DESC, audit_id LIMIT ?",
            (limit,),
        )
