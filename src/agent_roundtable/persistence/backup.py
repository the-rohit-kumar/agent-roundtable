"""Consistent, checksummed SQLite backups and safe managed invalidation."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso


class BackupService:
    def __init__(self, db: Database, root: Path) -> None:
        self.db, self.root = db, Path(root).resolve()

    async def create(self) -> str:
        backup_id = str(uuid4())
        folder = self.root / backup_id
        folder.mkdir(parents=True, exist_ok=False)
        target = folder / "database.sqlite"
        async with self.db._lock:
            source = self.db._ensure_open()
            await __import__("asyncio").to_thread(self._copy, source, target)
        digest = self._sha(target)
        conn = sqlite3.connect(target)
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[
            0
        ]
        conn.close()
        manifest = {
            "backup_id": backup_id,
            "created_at": utc_now_iso(),
            "schema_version": version,
            "files": [
                {"name": target.name, "size": target.stat().st_size, "sha256": digest}
            ],
        }
        (folder / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        await self.db.execute(
            "INSERT INTO backup_runs(backup_id,destination,sha256,status,created_at,completed_at,metadata_json) "
            "VALUES(?,?,?,'completed',?,?,?)",
            (
                backup_id,
                str(folder),
                digest,
                manifest["created_at"],
                manifest["created_at"],
                json.dumps(manifest),
            ),
        )
        return backup_id

    @staticmethod
    def _copy(source: sqlite3.Connection, target: Path) -> None:
        out = sqlite3.connect(target)
        try:
            source.backup(out)
        finally:
            out.close()

    @staticmethod
    def _sha(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    async def list(self) -> list[dict]:
        return await self.db.fetch_all(
            "SELECT backup_id,destination,sha256,status,created_at FROM backup_runs ORDER BY created_at"
        )

    async def verify(self, backup_id: str) -> dict:
        row = await self.db.fetch_one(
            "SELECT destination,sha256 FROM backup_runs WHERE backup_id=?", (backup_id,)
        )
        if not row:
            raise ValueError("backup not found")
        folder = self._managed(row["destination"])
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        dbfile = folder / "database.sqlite"
        if (
            self._sha(dbfile) != row["sha256"]
            or manifest["files"][0]["sha256"] != row["sha256"]
        ):
            raise ValueError("backup checksum mismatch")
        conn = sqlite3.connect(dbfile)
        try:
            version = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if version != manifest["schema_version"] or integrity != "ok":
            raise ValueError("backup schema or integrity check failed")
        from agent_roundtable.persistence.migrations.versions import MIGRATIONS

        known_versions = {migration[0] for migration in MIGRATIONS}
        if version not in known_versions:
            raise ValueError(
                "backup schema version is not supported by this application"
            )
        return manifest

    def _managed(self, destination: str) -> Path:
        path = Path(destination).resolve()
        if path.parent != self.root or path.name in {"", "."}:
            raise ValueError("backup path is outside the managed backup root")
        return path

    async def invalidate_all(self) -> None:
        rows = await self.db.fetch_all("SELECT backup_id,destination FROM backup_runs")
        for row in rows:
            folder = self._managed(row["destination"])
            if folder.exists():
                shutil.rmtree(folder)
            await self.db.execute(
                "UPDATE backup_runs SET status='invalidated' WHERE backup_id=?",
                (row["backup_id"],),
            )

    async def restore(self, backup_id: str) -> None:
        await self.verify(backup_id)
        row = await self.db.fetch_one(
            "SELECT destination FROM backup_runs WHERE backup_id=?", (backup_id,)
        )
        source = self._managed(row["destination"]) / "database.sqlite"
        # Keep a pre-restore copy before replacing the current database.
        await self.create()
        history = await self.db.fetch_all("SELECT * FROM backup_runs")
        await self.db.close()
        shutil.copy2(source, self.db.path)
        for suffix in ("-wal", "-shm"):
            Path(str(self.db.path) + suffix).unlink(missing_ok=True)
        # The restored point-in-time database predates some backup index rows.
        # Preserve the durable external backup history so each archive remains
        # discoverable and verifiable after restore.
        conn = sqlite3.connect(self.db.path)
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO backup_runs(backup_id,destination,sha256,status,created_at,completed_at,metadata_json) "
                "VALUES(?,?,?,?,?,?,?)",
                [
                    tuple(
                        item[key]
                        for key in (
                            "backup_id",
                            "destination",
                            "sha256",
                            "status",
                            "created_at",
                            "completed_at",
                            "metadata_json",
                        )
                    )
                    for item in history
                ],
            )
            conn.commit()
        finally:
            conn.close()
