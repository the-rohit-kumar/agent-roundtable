"""Two step owner confirmation and content-free deletion audit."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from agent_roundtable.persistence.backup import BackupService
from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso


class ForgetService:
    def __init__(self, db: Database, backups: BackupService) -> None:
        self.db, self.backups = db, backups

    async def preview(self, actor_id: str, memory_id: str) -> str | None:
        item = await self.db.fetch_one(
            "SELECT memory_id,scope,agent_id,project_id,category FROM memory_items WHERE memory_id=?",
            (memory_id,),
        )
        if item is None:
            return None
        token = secrets.token_urlsafe(24)
        now = datetime.now(UTC)
        await self.db.execute(
            "INSERT INTO forget_confirmations VALUES(?,?,?,?,?,?,?,NULL)",
            (
                secrets.token_hex(16),
                actor_id,
                "memory",
                memory_id,
                hashlib.sha256(token.encode()).hexdigest(),
                now.isoformat(),
                (now + timedelta(minutes=10)).isoformat(),
            ),
        )
        return (
            f"Memory {memory_id}: {item['scope']} {item['category']} "
            f"(project {item['project_id'] or 'general'}). Confirming also removes "
            f"all managed backup archives. Confirm with token: {token}"
        )

    async def confirm(self, actor_id: str, memory_id: str, token: str) -> bool:
        row = await self.db.fetch_one(
            "SELECT * FROM forget_confirmations WHERE actor_id=? AND target_type='memory' AND target_id=? AND used_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (actor_id, memory_id),
        )
        if row is None or row["expires_at"] < utc_now_iso():
            return False
        expected = row["token_hash"]
        if not hmac.compare_digest(
            expected, hashlib.sha256(token.encode()).hexdigest()
        ):
            return False
        # Remove all app-managed backup copies before deleting the live content.
        await self.backups.invalidate_all()
        await self.db.execute(
            "DELETE FROM memory_items WHERE memory_id=?", (memory_id,)
        )
        await self.db.execute(
            "UPDATE forget_confirmations SET used_at=? WHERE confirmation_id=?",
            (utc_now_iso(), row["confirmation_id"]),
        )
        await self.db.execute(
            "INSERT INTO audit_events(audit_id,actor_type,actor_id,action,target_type,target_id,result,reason_code,correlation_id,created_at,details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                secrets.token_hex(16),
                "owner",
                actor_id,
                "memory_forget",
                "memory",
                memory_id,
                "success",
                "confirmed",
                secrets.token_hex(16),
                utc_now_iso(),
                "{}",
            ),
        )
        return True
