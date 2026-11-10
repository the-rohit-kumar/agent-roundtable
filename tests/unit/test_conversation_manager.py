from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_roundtable.domain.models import (
    AgentCapabilities,
    AgentRequest,
    AgentRunResult,
    AgentRunStatus,
    AuthorType,
    ConversationCreate,
    ConversationMode,
    ConversationOrigin,
    ConversationVisibility,
    MessageCreate,
)
from agent_roundtable.orchestration.conversations import ConversationManager
from agent_roundtable.orchestration.prompt_builder import PromptBuilder
from agent_roundtable.persistence.database import Database
from agent_roundtable.persistence.repositories.conversations import (
    ConversationRepository,
)
from agent_roundtable.persistence.repositories.decisions import DecisionRepository
from agent_roundtable.persistence.repositories.runs import (
    AgentRunRepository,
    ConversationTurnRepository,
)
from agent_roundtable.util import utc_now_iso


class FakeAdapter:
    def __init__(self, agent_id: str, response: str) -> None:
        self.agent_id = agent_id
        self.response = response
        self.requests: list[AgentRequest] = []

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(agent_id=self.agent_id, read_only_supported=True)

    async def health_check(self) -> Any:
        raise AssertionError("conversation manager must not launch extra health probes")

    async def cancel(self, run_id: Any) -> bool:
        return False

    async def run(self, request: AgentRequest) -> AgentRunResult:
        self.requests.append(request)
        answer = self.response.format(call=len(self.requests))
        now = utc_now_iso()
        return AgentRunResult(
            run_id=request.run_id,
            agent_id=self.agent_id,
            status=AgentRunStatus.SUCCESS,
            response_text=answer,
            started_at=now,
            finished_at=now,
        )


async def _manager(tmp_db: Path, tmp_path: Path, mode: ConversationMode):
    db = Database(tmp_db)
    await db.initialize()
    conversations = ConversationRepository(db)
    conversation = await conversations.create(
        ConversationCreate(
            mode=mode,
            origin=ConversationOrigin.FRIEND,
            visibility=ConversationVisibility.SHARED,
        )
    )
    await conversations.append_message(
        MessageCreate(
            conversation_id=conversation.conversation_id,
            author_type=AuthorType.FRIEND,
            author_id="friend-1",
            content="[topic] Compare these options",
            visibility=ConversationVisibility.SHARED,
        )
    )
    codex = FakeAdapter("codex", "Codex position {call}")
    antigravity = FakeAdapter("antigravity", "Antigravity position {call}")
    persona_paths = {}
    for agent_id in ("codex", "antigravity"):
        path = tmp_path / f"{agent_id}.md"
        path.write_text(f"{agent_id} persona", encoding="utf-8")
        persona_paths[agent_id] = str(path)
    manager = ConversationManager(
        conversations=conversations,
        turns=ConversationTurnRepository(db),
        runs=AgentRunRepository(db),
        adapters={"codex": codex, "antigravity": antigravity},
        prompt_builder=PromptBuilder(persona_paths),
        decisions=DecisionRepository(db),
    )
    return db, conversations, conversation, manager, codex, antigravity


@pytest.mark.asyncio
async def test_open_room_persists_agent_turn_and_message_linkage(
    tmp_db, tmp_path
) -> None:
    db, conversations, conversation, manager, codex, antigravity = await _manager(
        tmp_db, tmp_path, ConversationMode.OPEN_ROOM
    )
    published: list[str] = []

    async def publish(content: str) -> str:
        published.append(content)
        return "discord-message-1"

    await manager.process_user_turn(
        conversation.conversation_id,
        trigger_reason="user_started_conversation",
        publish=publish,
    )

    turns = await db.fetch_all("SELECT * FROM conversation_turns")
    runs = await db.fetch_all("SELECT * FROM agent_runs")
    messages = await conversations.get_context(conversation.conversation_id)
    agent_messages = [m for m in messages if m["author_type"] == "agent"]
    assert [row["agent_id"] for row in turns] == ["codex"]
    assert len(runs) == 1
    assert len(agent_messages) == 1
    assert agent_messages[0]["provider_run_id"] == runs[0]["run_id"]
    assert agent_messages[0]["discord_message_id"] == "discord-message-1"
    assert '"turn_index": 1' in agent_messages[0]["metadata_json"]
    assert "Codex" in published[0]
    assert len(codex.requests) == 1
    assert not antigravity.requests
    assert not codex.requests[0].working_directory
    await db.close()


@pytest.mark.asyncio
async def test_open_room_next_explicit_user_turn_alternates_by_policy(
    tmp_db, tmp_path
) -> None:
    db, _conversations, conversation, manager, codex, antigravity = await _manager(
        tmp_db, tmp_path, ConversationMode.OPEN_ROOM
    )
    await manager.process_user_turn(
        conversation.conversation_id, trigger_reason="user_started_conversation"
    )
    await _conversations.append_message(
        MessageCreate(
            conversation_id=conversation.conversation_id,
            author_type=AuthorType.FRIEND,
            author_id="friend-1",
            content="A follow-up",
            visibility=ConversationVisibility.SHARED,
        )
    )
    await manager.process_user_turn(
        conversation.conversation_id, trigger_reason="user_replied"
    )
    turns = await db.fetch_all(
        "SELECT agent_id, turn_index FROM conversation_turns ORDER BY turn_index"
    )
    assert [(row["agent_id"], row["turn_index"]) for row in turns] == [
        ("codex", 1),
        ("antigravity", 2),
    ]
    assert len(codex.requests) == len(antigravity.requests) == 1
    await db.close()


@pytest.mark.asyncio
async def test_debate_runs_independent_positions_then_records_proposed_synthesis(
    tmp_db, tmp_path
) -> None:
    db, conversations, conversation, manager, codex, antigravity = await _manager(
        tmp_db, tmp_path, ConversationMode.DEBATE
    )
    await manager.process_user_turn(
        conversation.conversation_id, trigger_reason="user_started_conversation"
    )
    turns = await db.fetch_all(
        "SELECT agent_id, turn_index, trigger_reason FROM conversation_turns "
        "ORDER BY turn_index"
    )
    decisions = await db.fetch_all("SELECT * FROM decisions")
    messages = await conversations.get_context(conversation.conversation_id)
    assert [row["agent_id"] for row in turns] == ["codex", "antigravity", "codex"]
    assert turns[-1]["trigger_reason"] == "structured_synthesis"
    assert len(codex.requests) == 2 and len(antigravity.requests) == 1
    assert "unresolved disagreement" in codex.requests[-1].prompt
    assert "Codex position 1" in codex.requests[-1].prompt
    assert "Antigravity position 1" in codex.requests[-1].prompt
    assert len(decisions) == 1 and decisions[0]["status"] == "proposed"
    assert decisions[0]["decision_text"] == "Codex position 2"
    assert len([row for row in messages if row["author_type"] == "agent"]) == 3
    await db.close()
