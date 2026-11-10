"""Memory proposals and strictly scoped context retrieval."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso


@dataclass(frozen=True)
class ContextRequest:
    agent_id: str
    conversation_id: str
    project_id: str | None
    purpose: str
    visibility: str
    origin: str
    authorized_source_ids: tuple[str, ...] = ()


class MemoryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def propose(
        self,
        *,
        scope: str,
        agent_id: str,
        project_id: str | None,
        category: str,
        content: str,
        confidence: str = "medium",
        source_refs: list[str] | None = None,
    ) -> str:
        if scope not in {"private", "shared"}:
            raise ValueError("scope must be private or shared")
        if scope == "shared" and not project_id:
            raise ValueError("shared memory must belong to a project")
        memory_id, now = str(uuid4()), utc_now_iso()
        await self.db.execute(
            """INSERT INTO memory_items(memory_id,scope,agent_id,project_id,category,content,
               status,confidence,source_refs_json,created_at,updated_at,created_by)
               VALUES(?,?,?,?,?,?,'candidate',?,?,?, ?,?)""",
            (
                memory_id,
                scope,
                agent_id if scope == "private" else None,
                project_id,
                category,
                content,
                confidence,
                json.dumps(source_refs or []),
                now,
                now,
                agent_id,
            ),
        )
        return memory_id

    async def list_items(self) -> list[dict]:
        return await self.db.fetch_all(
            "SELECT memory_id,scope,agent_id,project_id,category,status,confidence,created_at "
            "FROM memory_items ORDER BY created_at DESC"
        )

    async def get(self, memory_id: str) -> dict | None:
        return await self.db.fetch_one(
            "SELECT * FROM memory_items WHERE memory_id=?", (memory_id,)
        )

    async def approve_shared(self, memory_id: str, owner_id: str) -> bool:
        pending = await self.db.fetch_one(
            "SELECT memory_id FROM memory_items WHERE memory_id=? AND scope='shared' AND status='candidate'",
            (memory_id,),
        )
        if pending is None:
            return False
        await self.db.execute(
            "UPDATE memory_items SET status='accepted',approved_by=?,approved_at=?,updated_at=? "
            "WHERE memory_id=? AND scope='shared' AND status='candidate'",
            (owner_id, utc_now_iso(), utc_now_iso(), memory_id),
        )
        return True

    async def context(self, request: ContextRequest) -> list[dict]:
        # Friend context is denied before any memory query. Project IDs and source
        # IDs supplied by a friend never widen this boundary.
        if request.origin == "friend" or request.visibility == "shared":
            return []
        params: list[object] = [request.agent_id]
        private_sql = (
            "scope='private' AND agent_id=? AND status IN ('candidate','accepted')"
        )
        if request.project_id:
            private_sql += " AND (project_id IS NULL OR project_id=?)"
            params.append(request.project_id)
        else:
            private_sql += " AND project_id IS NULL"
        rows = await self.db.fetch_all(
            f"SELECT * FROM memory_items WHERE {private_sql}", params
        )
        if request.project_id:
            rows += await self.db.fetch_all(
                "SELECT * FROM memory_items WHERE scope='shared' AND status='accepted' AND project_id=?",
                (request.project_id,),
            )
        return rows

    async def delete(self, memory_id: str) -> bool:
        exists = await self.get(memory_id)
        if exists is None:
            return False
        await self.db.execute(
            "DELETE FROM memory_items WHERE memory_id=?", (memory_id,)
        )
        return True
