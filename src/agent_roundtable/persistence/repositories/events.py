"""Durable event queue with idempotency and restart recovery."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from agent_roundtable.domain.models import EventCreate, EventStatus
from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso


class EventRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def enqueue_if_new(self, event: EventCreate) -> tuple[dict[str, Any], bool]:
        now = utc_now_iso()

        def insert(conn):
            cursor = conn.execute(
                """INSERT OR IGNORE INTO events(event_id,event_type,source,project_id,conversation_id,
                   dedupe_key,visibility,payload_json,status,attempt_count,created_at,available_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(event.event_id),
                    event.event_type.value,
                    event.source,
                    str(event.project_id) if event.project_id else None,
                    str(event.conversation_id) if event.conversation_id else None,
                    event.dedupe_key,
                    event.visibility.value,
                    event.payload_json,
                    EventStatus.PENDING.value,
                    0,
                    now,
                    event.available_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM events WHERE event_type=? AND dedupe_key=?",
                (event.event_type.value, event.dedupe_key),
            ).fetchone()
            return dict(row), cursor.rowcount == 1

        return await self.db.run_transaction(insert)

    async def get(self, event_id: UUID | str) -> dict[str, Any] | None:
        return await self.db.fetch_one(
            "SELECT * FROM events WHERE event_id=?", (str(event_id),)
        )

    async def claim_next(self) -> dict[str, Any] | None:
        now = utc_now_iso()

        def claim(conn):
            row = conn.execute(
                "SELECT * FROM events WHERE status='pending' AND available_at<=? ORDER BY created_at,event_id LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE events SET status='processing',attempt_count=attempt_count+1 WHERE event_id=? AND status='pending'",
                (row["event_id"],),
            )
            result = dict(row)
            result["status"] = "processing"
            result["attempt_count"] += 1
            return result

        return await self.db.run_transaction(claim)

    async def complete(self, event_id: str) -> None:
        await self.db.execute(
            "UPDATE events SET status='completed',processed_at=?,failure_code=NULL WHERE event_id=?",
            (utc_now_iso(), event_id),
        )

    async def fail(
        self, event_id: str, reason_code: str, *, retryable: bool = False
    ) -> None:
        # Auth/quota/policy failures are terminal to avoid provider retry storms.
        status = EventStatus.PENDING.value if retryable else EventStatus.FAILED.value
        await self.db.execute(
            "UPDATE events SET status=?,failure_code=?,available_at=? WHERE event_id=?",
            (status, reason_code[:80], utc_now_iso(), event_id),
        )

    async def reject(self, event_id: str, reason_code: str) -> None:
        await self.db.execute(
            "UPDATE events SET status='rejected',failure_code=?,processed_at=? WHERE event_id=?",
            (reason_code[:80], utc_now_iso(), event_id),
        )

    async def recover_processing(self) -> int:
        return await self.db.execute(
            "UPDATE events SET status='pending',failure_code='controller_recovered' WHERE status='processing'"
        )

    async def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self.db.fetch_all(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
        )
