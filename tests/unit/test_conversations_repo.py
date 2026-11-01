"""Conversation/message/interaction repository persistence tests (§29.1)."""

from __future__ import annotations

import sqlite3
from uuid import uuid4

from agent_roundtable.domain.models import (
    AuthorType,
    ConversationCreate,
    ConversationOrigin,
    ConversationStatus,
    ConversationVisibility,
    MessageCreate,
)
from agent_roundtable.persistence.database import Database
from agent_roundtable.persistence.repositories.conversations import (
    ConversationRepository,
    InteractionRepository,
)


async def _db(tmp_db) -> Database:
    db = Database(tmp_db)
    await db.initialize()
    return db


async def test_create_and_get_round_trip(tmp_db) -> None:
    db = await _db(tmp_db)
    repo = ConversationRepository(db)
    conversation = await repo.create(
        ConversationCreate(
            origin=ConversationOrigin.FRIEND,
            visibility=ConversationVisibility.SHARED,
            discord_channel_id="123456",
            created_by_discord_id="999",
        )
    )
    row = await repo.get(conversation.conversation_id)
    assert row is not None
    assert row["project_id"] is None  # friends never carry a project (§25.3)
    assert row["visibility"] == ConversationVisibility.SHARED.value
    assert row["origin"] == ConversationOrigin.FRIEND.value
    assert row["status"] == ConversationStatus.OPEN.value
    await db.close()


async def test_set_status_and_closed_at(tmp_db) -> None:
    db = await _db(tmp_db)
    repo = ConversationRepository(db)
    conversation = await repo.create(
        ConversationCreate(origin=ConversationOrigin.OWNER)
    )
    await repo.set_status(
        conversation.conversation_id, ConversationStatus.WAITING_FOR_AGENT
    )
    row = await repo.get(conversation.conversation_id)
    assert row["status"] == ConversationStatus.WAITING_FOR_AGENT.value
    assert row["closed_at"] is None  # closing states only
    await repo.set_status(conversation.conversation_id, ConversationStatus.FAILED)
    row = await repo.get(conversation.conversation_id)
    assert row["closed_at"] is not None
    await db.close()


async def test_set_thread_persists(tmp_db) -> None:
    db = await _db(tmp_db)
    repo = ConversationRepository(db)
    conversation = await repo.create(
        ConversationCreate(origin=ConversationOrigin.OWNER)
    )
    await repo.set_thread(conversation.conversation_id, "thread-77")
    row = await repo.get(conversation.conversation_id)
    assert row["discord_thread_id"] == "thread-77"
    await db.close()


async def test_messages_append_count_context_order(tmp_db) -> None:
    db = await _db(tmp_db)
    repo = ConversationRepository(db)
    conversation = await repo.create(
        ConversationCreate(origin=ConversationOrigin.FRIEND)
    )
    for index in range(4):
        await repo.append_message(
            MessageCreate(
                conversation_id=conversation.conversation_id,
                author_type=AuthorType.FRIEND,
                author_id="999",
                content=f"message {index}",
                visibility=ConversationVisibility.SHARED,
            )
        )
    assert await repo.message_count(conversation.conversation_id) == 4
    context = await repo.get_context(conversation.conversation_id)
    assert [row["content"] for row in context] == [
        "message 0",
        "message 1",
        "message 2",
        "message 3",
    ]
    recent = await repo.get_context(conversation.conversation_id, limit=2)
    assert [row["content"] for row in recent] == ["message 2", "message 3"]
    await db.close()


async def test_message_fk_enforced_for_missing_conversation(tmp_db) -> None:
    db = await _db(tmp_db)
    repo = ConversationRepository(db)
    try:
        await repo.append_message(
            MessageCreate(
                conversation_id=uuid4(),
                author_type=AuthorType.FRIEND,
                author_id="999",
                content="orphan",
                visibility=ConversationVisibility.SHARED,
            )
        )
    except sqlite3.IntegrityError:
        pass  # fail-closed: FK violation rejected
    else:
        raise AssertionError("message insert for missing conversation must fail")
    await db.close()


async def test_interaction_idempotency_begin_complete(tmp_db) -> None:
    db = await _db(tmp_db)
    interactions = InteractionRepository(db)
    repo = ConversationRepository(db)
    assert await interactions.begin("i-1", "999", "talk") is True
    assert await interactions.begin("i-1", "999", "talk") is False  # duplicate
    conversation = await repo.create(
        ConversationCreate(origin=ConversationOrigin.OWNER)
    )
    await interactions.complete("i-1", conversation.conversation_id)
    row = await db.fetch_one(
        "SELECT * FROM discord_interactions WHERE interaction_id = 'i-1'"
    )
    assert row["status"] == "completed"
    assert row["conversation_id"] == str(conversation.conversation_id)
    await db.close()


async def test_interaction_begin_different_ids_both_fresh(tmp_db) -> None:
    db = await _db(tmp_db)
    interactions = InteractionRepository(db)
    assert await interactions.begin("i-a", "999", "status") is True
    assert await interactions.begin("i-b", "999", "status") is True
    await db.close()
