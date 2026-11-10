"""Schema migrations: versioned, checksummed, monotonic (ARCHITECTURE §29.3).

Rules enforced here:
- Every migration has an increasing integer version and SHA-256 checksum.
- Already-applied migrations are verified, never edited; a checksum mismatch is
  treated as tampering and is FATAL.
- A failed migration aborts startup (FATAL); no fresh empty database is created
  to mask the problem.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from agent_roundtable.persistence.migrations.versions import MIGRATIONS
from agent_roundtable.util import utc_now_iso


class MigrationError(RuntimeError):
    """Migration application or verification failed (FATAL)."""


def file_checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def create_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            checksum   TEXT NOT NULL
        )
        """
    )


def applied_versions_sync(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute(
        "SELECT version, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {int(row["version"]): str(row["checksum"]) for row in rows}


def apply_migrations_sync(conn: sqlite3.Connection, db_path: Path) -> None:
    """Apply all pending migrations; verify checksums of applied ones."""
    create_schema_migrations_table(conn)
    applied = applied_versions_sync(conn)

    expected_versions = [version for version, _, _ in MIGRATIONS]
    for version, checksum in applied.items():
        if version not in expected_versions:
            raise MigrationError(
                f"Database at {db_path} has migration {version} that is unknown to "
                "this build; refusing to continue (no silent coercion)"
            )
        expected_checksum = file_checksum(
            next(c for v, _, c in MIGRATIONS if v == version)
        )
        if checksum != expected_checksum:
            raise MigrationError(
                f"Applied migration {version} checksum mismatch for {db_path}; "
                "database may have been modified outside this application"
            )
        if version < max(expected_versions) and version not in applied:
            raise MigrationError(f"Gap in migration history at version {version}")

    for version, name, sql in MIGRATIONS:
        if version in applied:
            continue
        prior_versions = {v for v in applied if v < version}
        missing = [
            v for v in expected_versions if v < version and v not in prior_versions
        ]
        if missing:
            raise MigrationError(
                f"Cannot apply migration {version}; earlier migrations {missing} missing"
            )
        try:
            conn.executescript(sql)  # implicit commit boundary per migration
            checksum = file_checksum(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at, checksum) "
                "VALUES (?, ?, ?)",
                (version, utc_now_iso(), checksum),
            )
            applied[version] = checksum
        except sqlite3.Error as exc:
            raise MigrationError(
                f"Migration {version} ({name}) failed on {db_path}: {exc}"
            ) from exc


def verify_schema_integrity_sync(conn: sqlite3.Connection) -> bool:
    # quick_integrity_check emits NO rows when clean and one row per problem.
    rows = conn.execute("PRAGMA quick_integrity_check").fetchall()
    if not rows:
        return True
    return all(str(row[0]).lower() == "ok" for row in rows)
