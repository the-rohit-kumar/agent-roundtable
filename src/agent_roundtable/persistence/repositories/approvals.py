"""Owner decisions for explicitly gated actions."""

from __future__ import annotations

import json
from uuid import uuid4

from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso


class ApprovalRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def request(
        self,
        *,
        action_type: str,
        target_type: str,
        target_id: str,
        requested_by: str,
        payload: dict,
    ) -> str:
        approval_id = str(uuid4())
        await self.db.execute(
            "INSERT INTO approvals(approval_id,action_type,target_type,target_id,payload_json,status,requested_by,created_at) VALUES(?,?,?,?,?,'pending',?,?)",
            (
                approval_id,
                action_type,
                target_type,
                target_id,
                json.dumps(payload),
                requested_by,
                utc_now_iso(),
            ),
        )
        return approval_id

    async def decide(
        self, approval_id: str, owner_id: str, *, approved: bool, reason: str = ""
    ) -> None:
        def update(conn):
            row = conn.execute(
                "SELECT status FROM approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
            if row is None or row["status"] != "pending":
                raise ValueError("approval is not pending")
            conn.execute(
                "UPDATE approvals SET status=?,decided_by=?,decision_reason=?,decided_at=? WHERE approval_id=?",
                (
                    "approved" if approved else "rejected",
                    owner_id,
                    reason[:500],
                    utc_now_iso(),
                    approval_id,
                ),
            )

        await self.db.run_transaction(update)

    async def is_approved(
        self, action_type: str, target_type: str, target_id: str
    ) -> bool:
        row = await self.db.fetch_one(
            "SELECT approval_id FROM approvals WHERE action_type=? AND target_type=? AND target_id=? AND status='approved' ORDER BY decided_at DESC LIMIT 1",
            (action_type, target_type, target_id),
        )
        return row is not None
