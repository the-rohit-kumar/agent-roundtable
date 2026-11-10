"""Project registration persistence helpers."""

from __future__ import annotations

from agent_roundtable.persistence.database import Database


class ProjectRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, project_id: str) -> dict | None:
        return await self.db.fetch_one(
            "SELECT * FROM projects WHERE project_id=?", (project_id,)
        )

    async def by_name(self, name: str) -> dict | None:
        return await self.db.fetch_one(
            "SELECT * FROM projects WHERE name=? COLLATE NOCASE", (name,)
        )

    async def list(self) -> list[dict]:
        return await self.db.fetch_all(
            "SELECT * FROM projects ORDER BY name COLLATE NOCASE"
        )
