"""Conversation + message persistence (ConversationRepository, §26.4)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from agent_roundtable.domain.models import (
    ConversationCreate,
    ConversationStatus,
    MessageCreate,
)
from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso


class ConversationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, conversation: ConversationCreate) -> ConversationCreate:
        await self._db.execute(
            """
            INSERT INTO conversations (
                conversation_id, project_id, mode, visibility, origin, status,
                discord_channel_id, discord_thread_id, created_by_discord_id,
                trigger_event_id, created_at, updated_at, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(conversation.conversation_id),
                str(conversation.project_id) if conversation.project_id else None,
                conversation.mode.value,
                conversation.visibility.value,
                conversation.origin.value,
                conversation.status.value,
                conversation.discord_channel_id,
                conversation.discord_thread_id,
                conversation.created_by_discord_id,
                str(conversation.trigger_event_id)
                if conversation.trigger_event_id
                else None,
                conversation.created_at,
                conversation.updated_at,
                conversation.closed_at,
            ),
        )
        return conversation

    async def get(self, conversation_id: UUID) -> dict[str, Any] | None:
        return await self._db.fetch_one(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (str(conversation_id),),
        )

    async def set_status(
        self, conversation_id: UUID, status: ConversationStatus
    ) -> None:
        await self._db.execute(
            "UPDATE conversations SET status = ?, updated_at = ?, "
            "closed_at = CASE WHEN ? IN ('completed','stopped','failed') THEN ? ELSE closed_at END "
            "WHERE conversation_id = ?",
            (
                status.value,
                utc_now_iso(),
                status.value,
                utc_now_iso(),
                str(conversation_id),
            ),
        )

    async def set_thread(self, conversation_id: UUID, discord_thread_id: str) -> None:
        await self._db.execute(
            "UPDATE conversations SET discord_thread_id = ?, updated_at = ? WHERE conversation_id = ?",
            (discord_thread_id, utc_now_iso(), str(conversation_id)),
        )

    async def set_trigger_event(self, conversation_id: UUID, event_id: UUID) -> None:
        await self._db.execute(
            "UPDATE conversations SET trigger_event_id=?,updated_at=? WHERE conversation_id=?",
            (str(event_id), utc_now_iso(), str(conversation_id)),
        )

    # --- messages ---------------------------------------------------------

    async def append_message(self, message: MessageCreate) -> MessageCreate:
        await self._db.execute(
            """
            INSERT INTO messages (
                message_id, conversation_id, author_type, author_id, content,
                visibility, discord_message_id, provider_run_id,
                reply_to_message_id, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(message.message_id),
                str(message.conversation_id),
                message.author_type.value,
                message.author_id,
                message.content,
                message.visibility.value,
                message.discord_message_id,
                str(message.provider_run_id) if message.provider_run_id else None,
                str(message.reply_to_message_id)
                if message.reply_to_message_id
                else None,
                message.created_at,
                message.metadata_json,
            ),
        )
        return message

    async def get_conversation(self, conversation_id: UUID) -> list[dict[str, Any]]:
        return await self._db.fetch_all(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (str(conversation_id),),
        )

    async def get_context(
        self, conversation_id: UUID, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Recent messages oldest-first for context assembly (§26.4)."""
        rows = await self._db.fetch_all(
            "SELECT * FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at DESC, message_id LIMIT ?",
            (str(conversation_id), limit),
        )
        return list(reversed(rows))

    async def message_count(self, conversation_id: UUID) -> int:
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?",
            (str(conversation_id),),
        )
        return int(row["n"]) if row else 0

    async def set_message_discord_id(
        self, message_id: UUID, discord_message_id: str
    ) -> None:
        await self._db.execute(
            "UPDATE messages SET discord_message_id = ? WHERE message_id = ?",
            (discord_message_id, str(message_id)),
        )


class InteractionRepository:
    """Idempotency ledger for Discord interactions (§28.3)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def begin(
        self, interaction_id: str, discord_user_id: str, command_name: str
    ) -> bool:
        """True = we own this interaction (first delivery); False = duplicate."""
        try:
            await self._db.execute(
                """
                INSERT INTO discord_interactions (
                    interaction_id, discord_user_id, command_name, status, created_at
                ) VALUES (?, ?, ?, 'received', ?)
                """,
                (interaction_id, discord_user_id, command_name, utc_now_iso()),
            )
            return True
        except Exception as exc:  # UNIQUE violation -> duplicate delivery
            if "UNIQUE" in str(exc):
                return False
            raise

    async def complete(
        self, interaction_id: str, conversation_id: UUID | None = None
    ) -> None:
        await self._db.execute(
            "UPDATE discord_interactions SET status = 'completed', completed_at = ?, "
            "conversation_id = COALESCE(?, conversation_id) WHERE interaction_id = ?",
            (
                utc_now_iso(),
                str(conversation_id) if conversation_id else None,
                interaction_id,
            ),
        )


def messages_describe(rows: list[dict[str, Any]]) -> str:
    """Compact debug view; never includes secret-like content unredacted."""
    from agent_roundtable.observability.logging import redact

    return json.dumps(
        [
            {
                "id": r["message_id"],
                "author": r["author_id"],
                "preview": redact(r["content"])[:64],
            }
            for r in rows
        ]
    )
