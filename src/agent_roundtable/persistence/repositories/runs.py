"""Agent run and conversation-turn persistence (ARCHITECTURE §§27, 30)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from agent_roundtable.domain.models import AgentRequest, AgentRunResult
from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso


class AgentRunRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def begin(self, request: AgentRequest) -> None:
        await self._db.execute(
            """
            INSERT INTO agent_runs (
                run_id, agent_id, conversation_id, task_id, purpose, status,
                working_directory, permission_profile, started_at, usage_json,
                diagnostics_json, correlation_id
            ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, '{}', '[]', ?)
            """,
            (
                str(request.run_id),
                request.agent_id,
                str(request.conversation_id) if request.conversation_id else None,
                str(request.task_id) if request.task_id else None,
                request.purpose.value,
                request.working_directory,
                request.permission_profile,
                utc_now_iso(),
                str(request.correlation_id),
            ),
        )

    async def complete(self, result: AgentRunResult) -> AgentRunResult:
        await self._db.execute(
            "UPDATE agent_runs SET status = ?, provider_session_id = ?, "
            "exit_code = ?, provider_status = ?, finished_at = ?, "
            "duration_ms = ?, usage_json = ?, diagnostics_json = ? WHERE run_id = ?",
            (
                result.status.value,
                result.provider_session_id,
                result.exit_code,
                result.provider_status,
                result.finished_at,
                result.duration_ms,
                json.dumps(result.usage, ensure_ascii=False),
                json.dumps(result.diagnostics, ensure_ascii=False),
                str(result.run_id),
            ),
        )
        return result


class ConversationTurnRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(
        self,
        *,
        conversation_id: UUID,
        agent_id: str,
        run_id: UUID,
        trigger_reason: str,
        decision: str,
    ) -> int:
        def _insert(conn: Any) -> int:
            row = conn.execute(
                "SELECT COALESCE(MAX(turn_index), 0) + 1 AS next_index "
                "FROM conversation_turns WHERE conversation_id = ?",
                (str(conversation_id),),
            ).fetchone()
            turn_index = int(row["next_index"])
            conn.execute(
                """
                INSERT INTO conversation_turns (
                    turn_id, conversation_id, turn_index, agent_id, run_id,
                    trigger_reason, decision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(conversation_id),
                    turn_index,
                    agent_id,
                    str(run_id),
                    trigger_reason,
                    decision,
                    utc_now_iso(),
                ),
            )
            return turn_index

        return await self._db.run_transaction(_insert)

    async def list(self, conversation_id: UUID) -> list[dict[str, Any]]:
        return await self._db.fetch_all(
            "SELECT * FROM conversation_turns WHERE conversation_id = ? "
            "ORDER BY turn_index",
            (str(conversation_id),),
        )
