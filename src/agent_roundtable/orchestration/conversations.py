"""Bounded, persisted agent turns for shared conversations."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from agent_roundtable.adapters.base import AgentAdapter
from agent_roundtable.domain.models import (
    AgentRequest,
    AgentRunResult,
    AgentRunStatus,
    AuthorType,
    ConversationMode,
    ConversationStatus,
    ConversationVisibility,
    MessageCreate,
    RunPurpose,
)
from agent_roundtable.orchestration.prompt_builder import PromptBuilder
from agent_roundtable.orchestration.turn_policy import TurnPolicy
from agent_roundtable.persistence.repositories.conversations import (
    ConversationRepository,
)
from agent_roundtable.persistence.repositories.decisions import DecisionRepository
from agent_roundtable.persistence.repositories.memory import (
    ContextRequest,
    MemoryRepository,
)
from agent_roundtable.persistence.repositories.runs import (
    AgentRunRepository,
    ConversationTurnRepository,
)
from agent_roundtable.util import utc_now_iso

logger = logging.getLogger("agent_roundtable.orchestration")

Publisher = Callable[[str], Awaitable[str | None]]
DiscoverySink = Callable[[str, str, dict[str, Any]], Awaitable[None]]

_DISPLAY_NAMES = {"codex": "Codex", "antigravity": "Antigravity"}
DISPLAY_NAMES = _DISPLAY_NAMES


class ConversationManager:
    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        turns: ConversationTurnRepository,
        runs: AgentRunRepository,
        adapters: dict[str, AgentAdapter],
        prompt_builder: PromptBuilder,
        decisions: DecisionRepository | None = None,
        memories: MemoryRepository | None = None,
        discovery_sink: DiscoverySink | None = None,
        max_turns_per_cycle: int = 6,
    ) -> None:
        self._conversations = conversations
        self._turns = turns
        self._runs = runs
        self._adapters = adapters
        self._prompts = prompt_builder
        self._decisions = decisions
        self._memories = memories
        self._discovery_sink = discovery_sink
        self._policy = TurnPolicy()
        self._max_turns_per_cycle = max_turns_per_cycle
        self._locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def process_user_turn(
        self,
        conversation_id: UUID,
        *,
        trigger_reason: str,
        publish: Publisher | None = None,
        stream_session_factory: Callable[[str, int, ConversationMode], Awaitable[Any]]
        | None = None,
    ) -> list[AgentRunResult]:
        """Run one bounded policy cycle for an already-persisted user message."""
        async with self._locks[conversation_id]:
            row = await self._conversations.get(conversation_id)
            if row is None:
                raise ValueError("conversation does not exist")
            mode = ConversationMode(row["mode"])
            visibility = ConversationVisibility(row["visibility"])
            transcript = await self._conversations.get_context(
                conversation_id, limit=50
            )
            prior_turns = await self._turns.list(conversation_id)
            available = tuple(
                agent_id
                for agent_id, adapter in self._adapters.items()
                if adapter.capabilities().read_only_supported
            )
            selection = self._policy.select(
                mode=mode,
                available_agents=available,
                prior_turns=prior_turns,
                trigger_reason=trigger_reason,
            )
            agents = selection.agents[: self._max_turns_per_cycle]
            if not agents:
                notice = "No agent is currently available for this discussion."
                message = await self._conversations.append_message(
                    MessageCreate(
                        conversation_id=conversation_id,
                        author_type=AuthorType.SYSTEM,
                        author_id="controller",
                        content=notice,
                        visibility=visibility,
                    )
                )
                if publish is not None:
                    discord_id = await publish(notice)
                    if discord_id:
                        await self._conversations.set_message_discord_id(
                            message.message_id, discord_id
                        )
                await self._conversations.set_status(
                    conversation_id, ConversationStatus.WAITING_FOR_USER
                )
                return []

            requests = []
            for agent_id in agents:
                requests.append(
                    await self._make_request(
                        agent_id=agent_id,
                        conversation_id=conversation_id,
                        mode=mode,
                        transcript=transcript,
                        trigger_reason=trigger_reason,
                        context_request=ContextRequest(
                            agent_id=agent_id,
                            conversation_id=str(conversation_id),
                            project_id=row.get("project_id"),
                            purpose=trigger_reason,
                            visibility=visibility.value,
                            origin=row.get("origin", "owner"),
                        ),
                    )
                )
            turn_indices: list[int] = []
            for agent_id, request in zip(agents, requests, strict=True):
                await self._runs.begin(request)
                turn_indices.append(
                    await self._turns.append(
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        run_id=request.run_id,
                        trigger_reason=trigger_reason,
                        decision=selection.reason,
                    )
                )
            stream_sessions: dict[str, Any] = {}
            if stream_session_factory is not None:
                for agent_id, turn_index in zip(agents, turn_indices, strict=True):
                    try:
                        session = await stream_session_factory(
                            agent_id, turn_index, mode
                        )
                        await session.start()
                        stream_sessions[agent_id] = session
                    except Exception:
                        logger.warning(
                            "failed to initialize stream session for %s",
                            agent_id,
                            exc_info=True,
                        )

            results = await asyncio.gather(
                *(
                    self._run_one(
                        agent_id,
                        request,
                        on_stream_delta=stream_sessions[agent_id].on_delta
                        if agent_id in stream_sessions
                        else None,
                    )
                    for agent_id, request in zip(agents, requests, strict=True)
                )
            )

            # Persist in policy order even when structured-mode provider calls ran
            # concurrently, preserving a stable, reviewable transcript.
            evidence_message_ids: list[str] = []
            for agent_id, request, result, turn_index in zip(
                agents, requests, results, turn_indices, strict=True
            ):
                await self._runs.complete(result)
                session = stream_sessions.get(agent_id)
                if result.status is AgentRunStatus.SUCCESS and result.response_text:
                    if self._discovery_sink is not None:
                        await self._discovery_sink(agent_id, result.response_text, row)
                    metadata = json.dumps(
                        {
                            "mode": mode.value,
                            "turn_index": turn_index,
                            "trigger_reason": trigger_reason,
                            "provider_session_id": result.provider_session_id,
                        },
                        ensure_ascii=False,
                    )
                    message = await self._conversations.append_message(
                        MessageCreate(
                            conversation_id=conversation_id,
                            author_type=AuthorType.AGENT,
                            author_id=agent_id,
                            content=result.response_text,
                            visibility=visibility,
                            provider_run_id=result.run_id,
                            metadata_json=metadata,
                        )
                    )
                    evidence_message_ids.append(str(message.message_id))
                    label = _DISPLAY_NAMES.get(agent_id, agent_id)
                    formatted = (
                        f"**{label} · {mode.value.replace('_', ' ').title()} · "
                        f"turn {turn_index}**\n{result.response_text}"
                    )
                    discord_id = None
                    if session is not None:
                        discord_id = await session.finish(formatted)
                    elif publish is not None:
                        discord_id = await publish(formatted)
                    if discord_id:
                        await self._conversations.set_message_discord_id(
                            message.message_id, discord_id
                        )
                else:
                    if session is not None:
                        await session.abort()
                    if result.status is not AgentRunStatus.SUCCESS:
                        logger.info(
                            "agent turn did not produce a response",
                            extra={"agent_id": agent_id, "status": result.status.value},
                        )

            # Debate and Decision get one bounded synthesis pass after the
            # independent positions are archived. It preserves dissent instead
            # of silently selecting whichever provider responded last.
            if (
                mode in (ConversationMode.DEBATE, ConversationMode.DECISION)
                and len(evidence_message_ids) >= 2
                and len(results) < self._max_turns_per_cycle
            ):
                synth_agent = "codex" if "codex" in available else agents[0]
                synth_transcript = await self._conversations.get_context(
                    conversation_id, limit=50
                )
                synth_request = await self._make_request(
                    agent_id=synth_agent,
                    conversation_id=conversation_id,
                    mode=mode,
                    transcript=synth_transcript,
                    trigger_reason="structured_synthesis",
                )
                await self._runs.begin(synth_request)
                turn_index = await self._turns.append(
                    conversation_id=conversation_id,
                    agent_id=synth_agent,
                    run_id=synth_request.run_id,
                    trigger_reason="structured_synthesis",
                    decision=f"{mode.value}_preserve_dissent",
                )
                synth_session = None
                if stream_session_factory is not None:
                    try:
                        synth_session = await stream_session_factory(
                            synth_agent, turn_index, mode
                        )
                        await synth_session.start()
                    except Exception:
                        logger.warning(
                            "failed to initialize synth stream session",
                            exc_info=True,
                        )
                synth_result = await self._run_one(
                    synth_agent,
                    synth_request,
                    on_stream_delta=synth_session.on_delta if synth_session else None,
                )
                results.append(synth_result)
                await self._runs.complete(synth_result)
                if (
                    synth_result.status is AgentRunStatus.SUCCESS
                    and synth_result.response_text
                ):
                    position_evidence_ids = list(evidence_message_ids)
                    synth_message = await self._conversations.append_message(
                        MessageCreate(
                            conversation_id=conversation_id,
                            author_type=AuthorType.AGENT,
                            author_id=synth_agent,
                            content=synth_result.response_text,
                            visibility=visibility,
                            provider_run_id=synth_result.run_id,
                            metadata_json=json.dumps(
                                {
                                    "mode": mode.value,
                                    "turn_index": turn_index,
                                    "trigger_reason": "structured_synthesis",
                                    "synthesized_message_ids": position_evidence_ids,
                                    "provider_session_id": synth_result.provider_session_id,
                                },
                                ensure_ascii=False,
                            ),
                        )
                    )
                    evidence_message_ids.append(str(synth_message.message_id))
                    synth_formatted = (
                        f"**{_DISPLAY_NAMES.get(synth_agent, synth_agent)} · "
                        f"{mode.value.replace('_', ' ').title()} synthesis · "
                        f"turn {turn_index}**\n{synth_result.response_text}"
                    )
                    discord_id = None
                    if synth_session is not None:
                        discord_id = await synth_session.finish(synth_formatted)
                    elif publish is not None:
                        discord_id = await publish(synth_formatted)
                    if discord_id:
                        await self._conversations.set_message_discord_id(
                            synth_message.message_id, discord_id
                        )
                    if self._decisions is not None:
                        topic = next(
                            (
                                str(item["content"])[8:]
                                for item in transcript
                                if str(item["content"]).startswith("[topic] ")
                            ),
                            "Discussion conclusion",
                        )
                        await self._decisions.create_proposal(
                            conversation_id=conversation_id,
                            title=topic,
                            decision_text=synth_result.response_text,
                            evidence_message_ids=position_evidence_ids,
                        )
                elif synth_session is not None:
                    await synth_session.abort()

            await self._conversations.set_status(
                conversation_id, ConversationStatus.WAITING_FOR_USER
            )
            if not any(
                result.status is AgentRunStatus.SUCCESS and result.response_text
                for result in results
            ):
                notice = "The agent turn could not be completed. The result was saved for the owner."
                message = await self._conversations.append_message(
                    MessageCreate(
                        conversation_id=conversation_id,
                        author_type=AuthorType.SYSTEM,
                        author_id="controller",
                        content=notice,
                        visibility=visibility,
                    )
                )
                if publish is not None:
                    discord_id = await publish(notice)
                    if discord_id:
                        await self._conversations.set_message_discord_id(
                            message.message_id, discord_id
                        )
            return results

    async def _make_request(
        self,
        *,
        agent_id: str,
        conversation_id: UUID,
        mode: ConversationMode,
        transcript: list[dict[str, Any]],
        trigger_reason: str,
        context_request: ContextRequest | None = None,
    ) -> AgentRequest:
        purpose = {
            ConversationMode.RESEARCH: RunPurpose.RESEARCH,
            ConversationMode.CODE_REVIEW: RunPurpose.REVIEW,
        }.get(mode, RunPurpose.DISCUSSION)
        memories = (
            await self._memories.context(context_request)
            if self._memories and context_request
            else []
        )
        prompt = self._prompts.build(
            agent_id=agent_id,
            mode=mode,
            transcript=transcript,
            trigger_reason=trigger_reason,
            memories=memories,
        )
        return AgentRequest(
            agent_id=agent_id,
            purpose=purpose,
            conversation_id=conversation_id,
            permission_profile="discussion",
            prompt=prompt,
        )

    async def _run_one(
        self,
        agent_id: str,
        request: AgentRequest,
        on_stream_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AgentRunResult:
        try:
            adapter = self._adapters[agent_id]
            if on_stream_delta is not None:
                sig = inspect.signature(adapter.run)
                if "on_stream_delta" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                ):
                    return await adapter.run(request, on_stream_delta=on_stream_delta)
            return await adapter.run(request)
        except Exception as exc:  # noqa: BLE001 - provider boundary normalization
            # Provider errors are persisted without exposing raw stderr or
            # exception strings in shared channels or logs.
            logger.warning(
                "provider adapter failed",
                extra={"agent_id": agent_id, "error_type": type(exc).__name__},
            )
            return AgentRunResult(
                run_id=request.run_id,
                agent_id=agent_id,
                status=AgentRunStatus.ERROR,
                started_at=utc_now_iso(),
                finished_at=utc_now_iso(),
                reason_code="adapter_error",
                diagnostics=[type(exc).__name__],
            )
