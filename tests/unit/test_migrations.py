"""Migrations, pragmas, and integrity (ARCHITECTURE §12, §29)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_roundtable.persistence.database import Database, FatalDatabaseError
from agent_roundtable.persistence.migrations import MigrationError, file_checksum
from agent_roundtable.persistence.migrations.versions import MIGRATIONS

REQUIRED_TABLES = {
    "agents",
    "projects",
    "conversations",
    "messages",
    "agent_runs",
    "events",
    "tasks",
    "task_runs",
    "worktrees",
    "write_locks",
    "approvals",
    "memory_items",
    "decisions",
    "disagreements",
    "artifacts",
    "usage_records",
    "audit_events",
    "discord_interactions",
    "backup_runs",
    "schema_migrations",
}


async def test_initialize_is_deterministic_and_idempotent(tmp_db: Path) -> None:
    db = Database(tmp_db)
    await db.initialize()
    await db.initialize()  # second apply must be a no-op, not an error
    async with db._lock:
        rows = await asyncio_all_tables(db._conn)
    assert REQUIRED_TABLES <= rows
    await db.close()


async def asyncio_all_tables(conn: sqlite3.Connection | None) -> set[str]:
    assert conn is not None
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


async def test_wal_and_foreign_keys_enforced(tmp_db: Path) -> None:
    db = Database(tmp_db)
    await db.initialize()
    conn = db._conn
    assert conn is not None
    assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
    assert str(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == "1"
    await db.close()


async def test_migration_checksum_recorded(tmp_db: Path) -> None:
    db = Database(tmp_db)
    await db.initialize()
    conn = db._conn
    assert conn is not None
    row = conn.execute("SELECT version, checksum FROM schema_migrations").fetchone()
    assert row["version"] == MIGRATIONS[-1][0]
    assert row["checksum"] == file_checksum(MIGRATIONS[-1][2])
    await db.close()


async def test_restart_preserves_records(tmp_db: Path) -> None:
    db = Database(tmp_db)
    await db.initialize()
    await db.execute(
        "INSERT INTO audit_events (audit_id, actor_type, actor_id, action, result,"
        " correlation_id, created_at, details_json)"
        " VALUES ('a1', 'controller', 'test', 'startup', 'success', 'cid', '2026-11-01T00:00:00Z', '{}')"
    )
    await db.close()

    db2 = Database(tmp_db)
    await db2.initialize()
    rows = await db2.fetch_all("SELECT * FROM audit_events WHERE audit_id='a1'")
    assert len(rows) == 1
    assert rows[0]["action"] == "startup"
    # restart also preserves migration history (single row, correct checksum)
    conn = db2._conn
    count_and_checksum = conn.execute(
        "SELECT COUNT(*) AS n, checksum FROM schema_migrations GROUP BY version"
    ).fetchall()
    assert len(count_and_checksum) == 1
    assert count_and_checksum[0]["checksum"] == file_checksum(MIGRATIONS[-1][2])
    await db2.close()


async def test_foreign_key_violation_rejected(tmp_db: Path) -> None:
    db = Database(tmp_db)
    await db.initialize()
    with pytest.raises(sqlite3.IntegrityError):
        # messages.conversation_id has an FK to conversations
        await db.execute(
            "INSERT INTO messages (message_id, conversation_id, author_type, author_id,"
            " content, visibility, created_at, metadata_json)"
            " VALUES ('m1', 'nope', 'system', 'sys', 'hi', 'owner_only', 'now', '{}')"
        )
    await db.close()


async def test_corrupt_database_is_fatal_not_silent(tmp_db: Path) -> None:
    db = Database(tmp_db)
    await db.initialize()
    await db.close()
    # Overwrite the file header with unrelated bytes -> integrity + migration checks fail.
    tmp_db.write_bytes(b"this is not a valid sqlite database at all. " * 20)

    db2 = Database(tmp_db)
    with pytest.raises(FatalDatabaseError):
        await db2.initialize()


def test_tampered_migration_checksum_detected(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An applied migration whose checksum changed on disk is tampering -> FATAL."""
    tmp_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(tmp_db, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    from agent_roundtable.persistence import migrations as mig

    mig.create_schema_migrations_table(conn)
    version, _name, sql = MIGRATIONS[-1]
    from agent_roundtable.util import utc_now_iso

    conn.executescript(sql)
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at, checksum) VALUES (?, ?, ?)",
        (version, utc_now_iso(), "deadbeef"),  # forged checksum
    )
    with pytest.raises(MigrationError):
        mig.apply_migrations_sync(conn, tmp_db)
    conn.close()
