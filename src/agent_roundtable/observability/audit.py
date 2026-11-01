"""Audit service: writes structured, append-only audit events (§33.4)."""

from __future__ import annotations

import json
from typing import Any

from agent_roundtable.domain.models import AuditEventCreate, AuditResult
from agent_roundtable.observability.logging import current_correlation_id, redact
from agent_roundtable.persistence.repositories.audit import AuditRepository
from agent_roundtable.util import utc_now_iso


class AuditService:
    def __init__(self, repository: AuditRepository) -> None:
        self._repo = repository

    async def record(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        result: AuditResult = AuditResult.SUCCESS,
        reason_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details = details or {}
        safe_details = json.dumps({k: redact(str(v)) for k, v in details.items()})
        await self._repo.append(
            AuditEventCreate(
                actor_type=actor_type,  # type: ignore[arg-type]
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                result=result,
                reason_code=reason_code,
                correlation_id=current_correlation_id(),
                created_at=utc_now_iso(),
                details_json=safe_details,
            )
        )
