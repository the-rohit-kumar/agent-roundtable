"""Small source adapters that normalize producer data into the event contract."""

from __future__ import annotations

import json
import re
from pathlib import PurePath, PureWindowsPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_roundtable.domain.models import EventType
from agent_roundtable.events.router import EventRouter


class AgentDiscoveryProposal(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal[
        "discussion_proposal", "research_proposal", "task_proposal", "no_action"
    ]
    title: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=4000)
    novelty_reason: str = Field(default="", max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)


class EventSources:
    def __init__(
        self,
        router: EventRouter,
        *,
        enabled: bool = False,
        agent_discoveries: bool = False,
        agent_follow_ups: bool = False,
    ) -> None:
        self.router = router
        self.enabled = enabled
        self.agent_discoveries = agent_discoveries
        self.agent_follow_ups = agent_follow_ups

    def _require(self, *, agent: bool = False, follow_up: bool = False) -> None:
        if not self.enabled:
            raise ValueError("autonomous event triggers are disabled by configuration")
        if agent and not self.agent_discoveries:
            raise ValueError("agent discovery events are disabled by configuration")
        if follow_up and not self.agent_follow_ups:
            raise ValueError(
                "agent-initiated discussions are disabled by configuration"
            )

    async def discord_message(
        self,
        *,
        user_id: str,
        message_id: str,
        content: str,
        conversation_id: UUID,
        visibility: str,
    ) -> tuple[dict, bool]:
        return await self.router.ingest(
            event_type=EventType.DISCORD_COMMAND,
            source="discord",
            summary=content,
            title="Discord discussion message",
            visibility=visibility,
            conversation_id=conversation_id,
            evidence_refs=[message_id],
            actor_id=user_id,
        )

    async def git_commit(
        self, *, project_id: UUID, revision: str, summary: str
    ) -> tuple[dict, bool]:
        self._require()
        return await self.router.ingest(
            event_type=EventType.GIT_COMMIT,
            source="git",
            summary=summary,
            title=f"Commit {revision[:12]}",
            visibility="owner_only",
            project_id=project_id,
            evidence_refs=[revision],
        )

    async def git_changed_paths(
        self, *, project_id: UUID, revision: str, paths: list[str], summary: str
    ) -> tuple[dict, bool]:
        self._require()
        if len(paths) > 30 or any(
            PurePath(path).is_absolute()
            or PureWindowsPath(path).is_absolute()
            or ".." in PurePath(path).parts
            or ".." in PureWindowsPath(path).parts
            for path in paths
        ):
            raise ValueError("changed path list is invalid")
        return await self.router.ingest(
            event_type=EventType.GIT_CHANGED_PATHS,
            source="git",
            summary=summary,
            title=f"Changed paths at {revision[:12]}",
            visibility="owner_only",
            project_id=project_id,
            evidence_refs=[revision, *paths],
        )

    async def test_result(
        self, *, project_id: UUID, run_id: str, passed: bool, summary: str
    ) -> tuple[dict, bool]:
        self._require()
        return await self.router.ingest(
            event_type=EventType.TEST_COMPLETED if passed else EventType.TEST_FAILED,
            source="test_runner",
            summary=summary,
            title="Test run result",
            visibility="owner_only",
            project_id=project_id,
            evidence_refs=[run_id],
        )

    async def task_completed(
        self, *, project_id: UUID, task_id: str, summary: str
    ) -> tuple[dict, bool]:
        self._require()
        return await self.router.ingest(
            event_type=EventType.TASK_COMPLETED,
            source="controller",
            summary=summary,
            title="Task completed",
            visibility="owner_only",
            project_id=project_id,
            evidence_refs=[task_id],
        )

    async def agent_discovery(
        self,
        *,
        agent_id: str,
        title: str,
        summary: str,
        evidence_refs: list[str],
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
        parent_event_id: str | None = None,
        chain_depth: int = 0,
    ) -> tuple[dict, bool]:
        self._require(agent=True, follow_up=True)
        if agent_id not in {"codex", "antigravity"}:
            raise ValueError("unknown agent source")
        return await self.router.ingest(
            event_type=EventType.AGENT_DISCOVERY,
            source="agent",
            summary=summary,
            title=title,
            visibility="owner_only",
            project_id=project_id,
            conversation_id=conversation_id,
            evidence_refs=evidence_refs,
            parent_event_id=parent_event_id,
            chain_depth=chain_depth,
            actor_id=agent_id,
        )

    async def consume_agent_output(
        self,
        *,
        agent_id: str,
        response: str,
        conversation_id: UUID | None = None,
        parent_event_id: str | None = None,
        chain_depth: int = 0,
    ) -> tuple[dict, bool] | None:
        """Accept only a structured, evidence-backed discussion proposal.

        Natural-language acknowledgements and malformed JSON are inert. Other
        proposal kinds are never executed in this milestone.
        """
        self._require(agent=True, follow_up=True)
        blocks = re.findall(
            r"```json\s*(.*?)```", response, flags=re.IGNORECASE | re.DOTALL
        )
        if not blocks:
            return None
        try:
            proposal = AgentDiscoveryProposal.model_validate(json.loads(blocks[-1]))
        except (json.JSONDecodeError, ValidationError):
            return None
        if (
            proposal.kind != "discussion_proposal"
            or not proposal.title.strip()
            or not proposal.summary.strip()
            or not proposal.evidence_refs
        ):
            return None
        return await self.agent_discovery(
            agent_id=agent_id,
            title=proposal.title,
            summary=f"{proposal.summary}\nNovelty: {proposal.novelty_reason}",
            evidence_refs=proposal.evidence_refs,
            conversation_id=conversation_id,
            parent_event_id=parent_event_id,
            chain_depth=chain_depth,
        )

    async def agent_follow_up(
        self,
        *,
        summary: str,
        evidence_refs: list[str],
        parent_event_id: str,
        chain_depth: int,
        project_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> tuple[dict, bool]:
        self._require(agent=True, follow_up=True)
        return await self.router.ingest(
            event_type=EventType.AGENT_FOLLOW_UP,
            source="agent",
            summary=summary,
            title="Agent follow-up",
            visibility="owner_only",
            project_id=project_id,
            conversation_id=conversation_id,
            evidence_refs=evidence_refs,
            parent_event_id=parent_event_id,
            chain_depth=chain_depth,
        )

    async def schedule_tick(
        self, *, schedule_id: str, occurred_at: str, summary: str
    ) -> tuple[dict, bool]:
        self._require()
        return await self.router.ingest(
            event_type=EventType.SCHEDULE_TICK,
            source="scheduler",
            summary=summary,
            title=f"Scheduled check: {schedule_id}",
            visibility="owner_only",
            evidence_refs=[occurred_at],
        )
