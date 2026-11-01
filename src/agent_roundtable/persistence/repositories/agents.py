"""Agent identity registry (agents table)."""

from __future__ import annotations

from typing import Any

from agent_roundtable.domain.models import AgentProvider, AgentSeed
from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso


class AgentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def ensure_agent(self, seed: AgentSeed) -> None:
        """Insert the agent identity if absent; leave existing rows untouched

        (owner-driven changes go through set_enabled, so re-registration cannot
        silently flip an admin-disabled provider back on)."""
        await self._db.execute(
            """
            INSERT INTO agents (agent_id, provider, display_name, persona_path,
                                enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO NOTHING
            """,
            (
                seed.agent_id,
                seed.provider.value,
                seed.display_name,
                seed.persona_path,
                1 if seed.enabled else 0,
                utc_now_iso(),
                utc_now_iso(),
            ),
        )

    async def list_agents(self) -> list[dict[str, Any]]:
        return await self._db.fetch_all("SELECT * FROM agents ORDER BY provider")

    async def get_by_provider(self, provider: AgentProvider) -> dict[str, Any] | None:
        return await self._db.fetch_one(
            "SELECT * FROM agents WHERE provider = ?", (provider.value,)
        )

    async def set_enabled(self, provider: AgentProvider, enabled: bool) -> None:
        await self._db.execute(
            "UPDATE agents SET enabled = ?, updated_at = ? WHERE provider = ?",
            (1 if enabled else 0, utc_now_iso(), provider.value),
        )
