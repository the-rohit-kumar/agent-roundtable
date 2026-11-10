"""Adapter protocol (§26.4). Constructors receive dependencies (DI) rather
than importing global singletons; adapters never touch the network directly
beyond their CLI child process."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from agent_roundtable.domain.models import (
    AgentCapabilities,
    AgentHealth,
    AgentRequest,
    AgentRunResult,
)


class AgentAdapter(Protocol):
    agent_id: str

    def capabilities(self) -> AgentCapabilities: ...
    async def health_check(self) -> AgentHealth: ...
    async def run(
        self,
        request: AgentRequest,
        *,
        on_stream_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentRunResult: ...
    async def cancel(self, run_id: UUID) -> bool: ...
