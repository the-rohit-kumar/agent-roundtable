"""SQLite persistence core (ARCHITECTURE §12, §29).

- One local database, WAL mode, foreign keys ON, bounded busy timeout.
- Async facade runs every SQLite operation off the event loop via
  `asyncio.to_thread` under an `asyncio.Lock` so transactions stay explicit and
  the Discord event loop is never blocked (DECISIONS D2).
- Migrations run before any other use; a failed or tampered migration is FATAL
  and never silently recreates a fresh database (§29.3).
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from agent_roundtable.persistence.migrations import (
    MigrationError,
    apply_migrations_sync,
    verify_schema_integrity_sync,
)

_PRAGMA_TIMEOUT_MS = 5000


class FatalDatabaseError(RuntimeError):
    """A required database invariant failed; the app must not execute agents."""


class Database:
    """Async facade over a single SQLite connection with explicit locking."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # --- lifecycle ---------------------------------------------------------

    def _connect_sync(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            self.path,
            timeout=_PRAGMA_TIMEOUT_MS / 1000,
            isolation_level=None,  # explicit BEGIN/COMMIT only
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is None:
            raise FatalDatabaseError(
                "Database is not initialized; call initialize() first"
            )
        return self._conn

    async def initialize(self) -> None:
        """Open, apply pragmas, verify integrity, and apply migrations (async)."""
        async with self._lock:
            await asyncio.to_thread(self._initialize_sync)

    def initialize_sync(self) -> None:
        """Synchronous variant for CLI contexts outside the event loop."""
        if self._lock.locked():  # pragma: no cover - defensive
            raise FatalDatabaseError("initialize() already in progress")
        self._initialize_sync()

    def _initialize_sync(self) -> None:
        brand_new = not self.path.exists()
        if self._conn is None:
            self._conn = self._connect_sync()
        conn = self._conn
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={_PRAGMA_TIMEOUT_MS}")
            if not brand_new and not verify_schema_integrity_sync(conn):
                raise FatalDatabaseError(
                    f"Database integrity check failed for {self.path}; "
                    "not creating a fresh empty database automatically"
                )
        except sqlite3.DatabaseError as exc:
            raise FatalDatabaseError(
                f"Database {self.path} is unreadable or not a valid SQLite database: {exc}"
            ) from exc
        try:
            apply_migrations_sync(conn, self.path)
        except MigrationError as exc:
            raise FatalDatabaseError(str(exc)) from exc

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None

    @property
    def initialized(self) -> bool:
        return self._conn is not None

    # --- generic helpers ---------------------------------------------------

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._execute_sync, sql, params)

    async def fetch_all(
        self, sql: str, params: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._fetch_all_sync, sql, params)

    async def fetch_one(
        self, sql: str, params: Sequence[Any] = ()
    ) -> dict[str, Any] | None:
        rows = await self.fetch_all(sql, params)
        return rows[0] if rows else None

    async def run_transaction(self, work: Callable[[sqlite3.Connection], Any]) -> Any:
        """Run `work(conn)` inside one explicit transaction, off the event loop."""
        async with self._lock:
            return await asyncio.to_thread(self._transaction_sync, work)

    # --- sync implementations (called via to_thread only) ------------------

    def _execute_sync(self, sql: str, params: Sequence[Any]) -> int:
        conn = self._ensure_open()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(sql, params)
            conn.execute("COMMIT")
            return cur.rowcount

    def _fetch_all_sync(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        conn = self._ensure_open()
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def _transaction_sync(self, work: Callable[[sqlite3.Connection], Any]) -> Any:
        conn = self._ensure_open()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = work(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return result
