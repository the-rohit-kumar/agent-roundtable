"""Structured implementation task persistence and validated transitions."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso

_TRANSITIONS = {
    "proposed": {"awaiting_approval", "queued", "rejected"},
    "awaiting_approval": {"queued", "rejected"},
    "queued": {"running", "canceled"},
    "running": {"validating", "failed", "canceled", "needs_changes"},
    "validating": {"committed_local", "needs_changes", "failed"},
    "needs_changes": {"running", "canceled"},
    "committed_local": {"completed", "needs_changes"},
    "rejected": set(),
    "failed": set(),
    "canceled": set(),
    "completed": set(),
}


class TaskRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(
        self,
        *,
        project_id: UUID,
        created_by: str,
        title: str,
        goal: str,
        acceptance: list[str],
        path_scopes: list[str],
        risk_level: str = "medium",
        preferred_writer: str | None = None,
    ) -> str:
        task_id, now = str(uuid4()), utc_now_iso()
        await self.db.execute(
            """INSERT INTO tasks(task_id,project_id,created_by,source,title,goal,acceptance_json,
               path_scope_json,risk_level,preferred_writer,status,created_at,updated_at,validation_json)
               VALUES(?,?,?,'owner',?,?,?,?,?,?,'proposed',?,?,'{}')""",
            (
                task_id,
                str(project_id),
                created_by,
                title[:200],
                goal,
                json.dumps(acceptance),
                json.dumps(path_scopes),
                risk_level,
                preferred_writer,
                now,
                now,
            ),
        )
        return task_id

    async def get(self, task_id: str | UUID) -> dict | None:
        row = await self.db.fetch_one(
            "SELECT * FROM tasks WHERE task_id=?", (str(task_id),)
        )
        if row:
            row["acceptance"] = json.loads(row["acceptance_json"])
            row["path_scopes"] = json.loads(row["path_scope_json"])
            row["validation"] = json.loads(row["validation_json"])
        return row

    async def list(self, project_id: str | None = None) -> list[dict]:
        if project_id:
            return await self.db.fetch_all(
                "SELECT t.task_id,t.project_id,t.title,t.status,t.preferred_writer,t.created_at,t.updated_at,"
                "(SELECT w.path FROM worktrees w WHERE w.task_id=t.task_id ORDER BY w.created_at DESC LIMIT 1) AS worktree_path "
                "FROM tasks t WHERE t.project_id=? ORDER BY t.created_at DESC",
                (project_id,),
            )
        return await self.db.fetch_all(
            "SELECT t.task_id,t.project_id,t.title,t.status,t.preferred_writer,t.created_at,t.updated_at,"
            "(SELECT w.path FROM worktrees w WHERE w.task_id=t.task_id ORDER BY w.created_at DESC LIMIT 1) AS worktree_path "
            "FROM tasks t ORDER BY t.created_at DESC"
        )

    async def transition(
        self, task_id: str, target: str, *, failure_code: str | None = None
    ) -> None:
        def update(conn):
            row = conn.execute(
                "SELECT status FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise ValueError("task not found")
            if target not in _TRANSITIONS.get(row["status"], set()):
                raise ValueError(f"invalid task transition {row['status']} -> {target}")
            conn.execute(
                "UPDATE tasks SET status=?,failure_code=?,updated_at=? WHERE task_id=?",
                (target, failure_code, utc_now_iso(), task_id),
            )

        await self.db.run_transaction(update)

    async def set_base_commit(self, task_id: str, commit: str) -> None:
        await self.db.execute(
            "UPDATE tasks SET base_commit=?,updated_at=? WHERE task_id=?",
            (commit, utc_now_iso(), task_id),
        )

    async def set_validation(
        self, task_id: str, evidence: dict, commit_sha: str | None = None
    ) -> None:
        await self.db.execute(
            "UPDATE tasks SET validation_json=?,commit_sha=?,completed_at=CASE WHEN ? IS NOT NULL THEN ? ELSE completed_at END,updated_at=? WHERE task_id=?",
            (
                json.dumps(evidence, ensure_ascii=False),
                commit_sha,
                commit_sha,
                utc_now_iso(),
                utc_now_iso(),
                task_id,
            ),
        )
