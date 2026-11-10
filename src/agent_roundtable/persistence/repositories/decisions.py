"""Decision records produced by structured conversation workflows."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso


class DecisionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_proposal(
        self,
        *,
        conversation_id: UUID,
        title: str,
        decision_text: str,
        evidence_message_ids: list[str],
    ) -> UUID:
        decision_id = uuid4()
        await self._db.execute(
            """
            INSERT INTO decisions (
                decision_id, project_id, conversation_id, title, status,
                decision_text, alternatives_json, evidence_json, dissent_json,
                created_at
            ) VALUES (?, NULL, ?, ?, 'proposed', ?, '[]', ?, '[]', ?)
            """,
            (
                str(decision_id),
                str(conversation_id),
                title[:200] or "Discussion conclusion",
                decision_text,
                json.dumps(evidence_message_ids),
                utc_now_iso(),
            ),
        )
        return decision_id
