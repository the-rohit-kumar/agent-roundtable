from pathlib import Path
from uuid import uuid4

import pytest

from agent_roundtable.domain.models import EventType
from agent_roundtable.events.router import EventRouter
from agent_roundtable.events.sources import EventSources
from agent_roundtable.persistence.database import Database
from agent_roundtable.persistence.repositories.events import EventRepository


async def build_router(path: Path) -> tuple[Database, EventRepository, EventRouter]:
    db = Database(path)
    await db.initialize()
    repo = EventRepository(db)
    return db, repo, EventRouter(repo, duplicate_window_minutes=30)


async def test_events_dedupe_and_are_processed_once(tmp_path: Path) -> None:
    db, repo, router = await build_router(tmp_path / "events.sqlite")
    sources = EventSources(router)
    conversation_id = uuid4()
    first, added = await sources.discord_message(
        user_id="owner",
        message_id="m1",
        content="Investigate this",
        conversation_id=conversation_id,
        visibility="owner_only",
    )
    duplicate, added_again = await sources.discord_message(
        user_id="owner",
        message_id="m1",
        content="Investigate this",
        conversation_id=conversation_id,
        visibility="owner_only",
    )
    assert added and not added_again
    assert first["event_id"] == duplicate["event_id"]

    seen = []

    async def handler(event: dict) -> None:
        seen.append(event["event_id"])

    assert await router.dispatch_event(first["event_id"], handler)
    assert not await router.dispatch_event(first["event_id"], handler)
    assert seen == [first["event_id"]]
    row = await repo.get(first["event_id"])
    assert row["status"] == "completed"
    assert row["attempt_count"] == 1
    await db.close()


async def test_dispatch_failure_is_terminal_and_recovery_requeues_interrupted_event(
    tmp_path: Path,
) -> None:
    db, repo, router = await build_router(tmp_path / "recovery.sqlite")
    event, _ = await router.ingest(
        event_type=EventType.TEST_FAILED,
        source="test_runner",
        summary="tests failed",
        visibility="owner_only",
        evidence_refs=["run-1"],
    )

    async def broken_handler(_: dict) -> None:
        raise RuntimeError("quota authentication error")

    assert await router.dispatch_event(event["event_id"], broken_handler)
    row = await repo.get(event["event_id"])
    assert row["status"] == "failed"
    assert row["attempt_count"] == 1
    assert not await router.dispatch_event(event["event_id"], broken_handler)

    second, _ = await router.ingest(
        event_type=EventType.GIT_COMMIT,
        source="git",
        summary="new commit",
        visibility="owner_only",
        evidence_refs=["revision-2"],
    )
    await repo.claim_next()
    assert await repo.recover_processing() == 1
    assert (await repo.get(second["event_id"]))["status"] == "pending"
    await db.close()


async def test_agent_followups_need_new_evidence_and_are_depth_bounded(
    tmp_path: Path,
) -> None:
    db, _, router = await build_router(tmp_path / "followups.sqlite")
    sources = EventSources(
        router, enabled=True, agent_discoveries=True, agent_follow_ups=True
    )
    with pytest.raises(ValueError, match="evidence"):
        await sources.agent_follow_up(
            summary="Thanks, acknowledged",
            evidence_refs=[],
            parent_event_id="e1",
            chain_depth=1,
        )
    with pytest.raises(ValueError, match="depth"):
        await sources.agent_discovery(
            agent_id="codex",
            title="Finding",
            summary="New finding",
            evidence_refs=["file:a"],
            parent_event_id="e1",
            chain_depth=3,
        )
    event, is_new = await sources.agent_discovery(
        agent_id="codex",
        title="Finding",
        summary="New finding",
        evidence_refs=["file:a"],
        parent_event_id="e1",
        chain_depth=1,
    )
    assert is_new and event["event_type"] == "agent.discovery"
    await db.close()


async def test_autonomous_sources_respect_disabled_configuration(
    tmp_path: Path,
) -> None:
    db, _, router = await build_router(tmp_path / "disabled.sqlite")
    sources = EventSources(router)
    with pytest.raises(ValueError, match="disabled"):
        await sources.git_commit(project_id=uuid4(), revision="abc", summary="commit")
    await db.close()


async def test_only_structured_evidence_backed_agent_proposals_create_events(
    tmp_path: Path,
) -> None:
    db, repo, router = await build_router(tmp_path / "discovery.sqlite")
    sources = EventSources(
        router, enabled=True, agent_discoveries=True, agent_follow_ups=True
    )
    assert (
        await sources.consume_agent_output(
            agent_id="codex", response="Thanks, acknowledged."
        )
        is None
    )
    response = """Finding
```json
{"kind":"discussion_proposal","title":"Check flaky test","summary":"The same test failed twice.","novelty_reason":"A second independent failure was recorded.","evidence_refs":["run-1","run-2"]}
```"""
    result = await sources.consume_agent_output(
        agent_id="codex", response=response, parent_event_id="root", chain_depth=1
    )
    assert result is not None and result[1]
    event = await repo.get(result[0]["event_id"])
    assert event["event_type"] == "agent.discovery"
    await db.close()
