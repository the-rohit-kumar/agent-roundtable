"""Explicit, bounded agent-selection policy for conversation turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_roundtable.domain.models import ConversationMode

_STRUCTURED_MODES = {
    ConversationMode.DEBATE,
    ConversationMode.RESEARCH,
    ConversationMode.BRAINSTORM,
    ConversationMode.CODE_REVIEW,
    ConversationMode.DECISION,
}


@dataclass(frozen=True)
class TurnSelection:
    agents: tuple[str, ...]
    reason: str


class TurnPolicy:
    """Use explicit mode and trigger reasons; never relay model turns forever."""

    def select(
        self,
        *,
        mode: ConversationMode,
        available_agents: tuple[str, ...],
        prior_turns: list[dict[str, Any]],
        trigger_reason: str,
    ) -> TurnSelection:
        if not available_agents:
            return TurnSelection((), "no_provider_available")
        if mode in _STRUCTURED_MODES:
            return TurnSelection(available_agents, f"{mode.value}_independent_review")
        if not prior_turns:
            chosen = "codex" if "codex" in available_agents else available_agents[0]
        else:
            previous = str(prior_turns[-1]["agent_id"])
            chosen = next(
                (agent for agent in available_agents if agent != previous),
                available_agents[0],
            )
        return TurnSelection((chosen,), f"open_room_{trigger_reason}")
