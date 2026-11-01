"""Repository round-trips and agent seeding."""

from __future__ import annotations

from agent_roundtable.domain.models import (
    AgentProvider,
    AgentSeed,
    AuditEventCreate,
    AuditResult,
)
from agent_roundtable.persistence.database import Database
from agent_roundtable.persistence.repositories.agents import AgentRepository
from agent_roundtable.persistence.repositories.audit import AuditRepository


async def _db(tmp_db):
    db = Database(tmp_db)
    await db.initialize()
    return db


async def test_agent_seed_and_list(tmp_db) -> None:
    db = await _db(tmp_db)
    repo = AgentRepository(db)
    await repo.ensure_agent(
        AgentSeed(
            agent_id="codex",
            provider=AgentProvider.CODEX,
            display_name="Codex Agent (Architect/Reviewer)",
            persona_path="prompts/codex_architect.md",
        )
    )
    # idempotent: second ensure is a no-op, does not duplicate or clobber
    await repo.ensure_agent(
        AgentSeed(
            agent_id="codex",
            provider=AgentProvider.CODEX,
            display_name="Renamed",
            persona_path="other.md",
        )
    )
    rows = await repo.list_agents()
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Codex Agent (Architect/Reviewer)"
    assert rows[0]["provider"] == "codex"
    await db.close()


async def test_agent_set_disabled_persists(tmp_db) -> None:
    db = await _db(tmp_db)
    repo = AgentRepository(db)
    await repo.ensure_agent(
        AgentSeed(
            agent_id="antigravity",
            provider=AgentProvider.ANTIGRAVITY,
            display_name="Antigravity Agent (Engineer/Investigator)",
            persona_path="prompts/antigravity_engineer.md",
        )
    )
    await repo.set_enabled(AgentProvider.ANTIGRAVITY, False)
    row = await repo.get_by_provider(AgentProvider.ANTIGRAVITY)
    assert row is not None and row["enabled"] == 0
    # A re-registration must not flip an admin-disabled provider back on.
    await repo.ensure_agent(
        AgentSeed(
            agent_id="antigravity",
            provider=AgentProvider.ANTIGRAVITY,
            display_name="Antigravity Agent (Engineer/Investigator)",
            persona_path="prompts/antigravity_engineer.md",
        )
    )
    row = await repo.get_by_provider(AgentProvider.ANTIGRAVITY)
    assert row["enabled"] == 0
    await db.close()


async def test_audit_append_and_recent(tmp_db) -> None:
    db = await _db(tmp_db)
    repo = AuditRepository(db)
    for i in range(3):
        await repo.append(
            AuditEventCreate(
                actor_type="controller",
                actor_id="test",
                action=f"action_{i}",
                result=AuditResult.SUCCESS,
                reason_code=None,
                correlation_id=f"cid-{i}",
                details_json="{}",
            )
        )
    events = await repo.recent(limit=2)
    assert len(events) == 2
    assert {e["action"] for e in events} <= {"action_0", "action_1", "action_2"}
    await db.close()
