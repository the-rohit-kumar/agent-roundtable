from pathlib import Path

from agent_roundtable.persistence.backup import BackupService
from agent_roundtable.persistence.database import Database
from agent_roundtable.persistence.forget import ForgetService
from agent_roundtable.persistence.repositories.memory import (
    ContextRequest,
    MemoryRepository,
)


async def test_memory_context_enforces_friend_agent_and_project_boundaries(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "state.sqlite")
    await db.initialize()
    repo = MemoryRepository(db)
    private = await repo.propose(
        scope="private",
        agent_id="codex",
        project_id="p1",
        category="fact",
        content="codex secret",
    )
    await repo.propose(
        scope="private",
        agent_id="antigravity",
        project_id="p1",
        category="fact",
        content="other agent",
    )
    shared = await repo.propose(
        scope="shared",
        agent_id="codex",
        project_id="p1",
        category="decision",
        content="shared decision",
    )
    assert await repo.context(
        ContextRequest("codex", "c", "p1", "discussion", "owner_only", "owner")
    ) == [await repo.get(private)]
    assert await repo.approve_shared(shared, "owner")
    rows = await repo.context(
        ContextRequest("codex", "c", "p1", "discussion", "owner_only", "owner")
    )
    assert {r["content"] for r in rows} == {"codex secret", "shared decision"}
    assert (
        await repo.context(
            ContextRequest("codex", "c", "p1", "discussion", "shared", "friend")
        )
        == []
    )
    assert all(r["content"] != "other agent" for r in rows)
    await db.close()


async def test_backup_verification_and_forget_confirmation_invalidation(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "state.sqlite")
    await db.initialize()
    memory = MemoryRepository(db)
    memory_id = await memory.propose(
        scope="private",
        agent_id="codex",
        project_id=None,
        category="preference",
        content="sensitive note",
    )
    backups = BackupService(db, tmp_path / "backups")
    backup_id = await backups.create()
    assert (await backups.verify(backup_id))["backup_id"] == backup_id
    forget = ForgetService(db, backups)
    preview = await forget.preview("owner", memory_id)
    assert preview and "sensitive note" not in preview
    token = preview.rsplit(" ", 1)[-1]
    assert not await forget.confirm("owner", memory_id, "wrong")
    assert await forget.confirm("owner", memory_id, token)
    assert await memory.get(memory_id) is None
    assert not (tmp_path / "backups" / backup_id).exists()
    run = await db.fetch_one(
        "SELECT status FROM backup_runs WHERE backup_id=?", (backup_id,)
    )
    assert run["status"] == "invalidated"
    audit = await db.fetch_one(
        "SELECT details_json FROM audit_events WHERE action='memory_forget'"
    )
    assert audit["details_json"] == "{}"
    await db.close()


async def test_restore_recovers_database_from_verified_backup(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.sqlite")
    await db.initialize()
    await db.execute(
        "INSERT INTO audit_events(audit_id,actor_type,actor_id,action,result,correlation_id,created_at,details_json) "
        "VALUES('before','owner','o','before_restore','success','c','now','{}')"
    )
    service = BackupService(db, tmp_path / "backups")
    backup_id = await service.create()
    await db.execute(
        "INSERT INTO audit_events(audit_id,actor_type,actor_id,action,result,correlation_id,created_at,details_json) "
        "VALUES('after','owner','o','after_backup','success','c','later','{}')"
    )
    await service.restore(backup_id)
    await db.initialize()
    rows = await db.fetch_all("SELECT audit_id FROM audit_events ORDER BY audit_id")
    assert [row["audit_id"] for row in rows] == ["before"]
    assert len(await service.list()) == 2  # original and pre-restore safety backup
    await db.close()
