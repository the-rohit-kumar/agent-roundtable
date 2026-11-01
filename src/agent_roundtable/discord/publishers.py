"""Response publishing: splitting, mention safety, visibility routing (§28.4)."""

from __future__ import annotations

from agent_roundtable.config import AppConfig
from agent_roundtable.domain.models import ConversationVisibility

#: Agent/command output must never ping anyone (§28.4: allowed mentions = none).
ALLOWED_MENTIONS_NONE_FIELD = "allowed_mentions"

DISCORD_MESSAGE_LIMIT = 2000


def split_for_discord(content: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split long content at safe boundaries; avoid breaking fenced code blocks.

    Strategy: prefer paragraph breaks, then line breaks, then hard cut. If a
    message would end mid-fence, extend to the fence close first.
    """
    if len(content) <= limit:
        return [content]

    chunks: list[str] = []
    current = ""
    paragraphs = content.split("\n\n")
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(_ensure_fence_balance(current))
            current = ""
        lines = paragraph.split("\n")
        for line in lines:
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(_ensure_fence_balance(current))
                current = ""
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
    if current:
        chunks.append(_ensure_fence_balance(current))
    return [c for c in chunks if c != ""]


def _ensure_fence_balance(chunk: str) -> str:
    fences = chunk.count("```")
    if fences % 2 == 1:
        chunk += "\n```"
    return chunk


def resolve_output_channel(
    config: AppConfig, visibility: ConversationVisibility
) -> str | None:
    """Configured channel for a visibility class; None -> publish in origin channel."""
    channels = config.discord.channels
    if visibility is ConversationVisibility.SHARED:
        return channels.open_room
    return channels.owner_operations or channels.agent_activity


def artifact_reference_text(artifact_name: str) -> str:
    """Short safe artifact pointer; never an absolute path in a friend channel (§28.4)."""
    safe_name = artifact_name.replace("\\", "/").split("/")[-1]
    return f"[artifact: {safe_name} — retrieve with /memory (owner-only)]"
