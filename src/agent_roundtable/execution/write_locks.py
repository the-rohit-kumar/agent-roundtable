"""Transactional path-prefix locks for concurrent project tasks."""

from __future__ import annotations

from pathlib import PurePosixPath
from uuid import uuid4

from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso


def normalize_scope(scope: str) -> str:
    value = scope.replace("\\", "/").strip("/")
    if value in {"", "."}:
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("write scope must be a relative path without traversal")
    return "/".join(path.parts).casefold()


def scopes_overlap(left: str, right: str) -> bool:
    a, b = normalize_scope(left), normalize_scope(right)
    return not a or not b or a == b or a.startswith(b + "/") or b.startswith(a + "/")


class WriteLockRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def acquire(
        self, project_id: str, task_id: str, agent_id: str, scopes: list[str]
    ) -> bool:
        normalized = sorted({normalize_scope(scope) for scope in (scopes or [""])})
        now = utc_now_iso()

        def lock(conn):
            existing = conn.execute(
                "SELECT path_prefix FROM write_locks WHERE project_id=? AND status='active'",
                (project_id,),
            ).fetchall()
            if any(
                scopes_overlap(candidate, row["path_prefix"])
                for candidate in normalized
                for row in existing
            ):
                return False
            for scope in normalized:
                conn.execute(
                    "INSERT INTO write_locks(lock_id,project_id,task_id,path_prefix,agent_id,acquired_at,status) "
                    "VALUES(?,?,?,?,?,?,'active')",
                    (str(uuid4()), project_id, task_id, scope, agent_id, now),
                )
            return True

        return await self.db.run_transaction(lock)

    async def release(self, task_id: str) -> None:
        await self.db.execute(
            "UPDATE write_locks SET status='released',released_at=? WHERE task_id=? AND status='active'",
            (utc_now_iso(), task_id),
        )

    async def active(self, project_id: str) -> list[dict]:
        return await self.db.fetch_all(
            "SELECT * FROM write_locks WHERE project_id=? AND status='active' ORDER BY acquired_at",
            (project_id,),
        )
