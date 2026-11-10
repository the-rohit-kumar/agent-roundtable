"""Response publishing: splitting, mention safety, visibility routing (§28.4)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import discord

from agent_roundtable.config import AppConfig
from agent_roundtable.domain.models import ConversationVisibility

logger = logging.getLogger("agent_roundtable.discord")

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


class DiscordStreamSession:
    """Manages throttled real-time streaming of an agent's response to Discord.

    - Posts an initial placeholder message: f"{header}\n*thinking...* ✍️"
    - Buffers incoming stream deltas and debounces Discord message edits to >= 1.0s
      intervals to strictly respect Discord API rate limits (~5 edits / 5s).
    - Removes the typing indicator upon completion, splitting long messages if
      content exceeds DISCORD_MESSAGE_LIMIT (2000 chars).
    - Fails open gracefully on edit errors without aborting provider execution.
    """

    def __init__(
        self,
        *,
        send_initial: Callable[[str], Awaitable[Any]],
        header: str,
        throttle_seconds: float = 1.0,
        send_followup: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._send_initial = send_initial
        self._send_followup = send_followup or send_initial
        self._header = header
        self._throttle_seconds = throttle_seconds
        self._message: Any | None = None
        self._accumulated: str = ""
        self._last_edit_time: float = 0.0
        self._edit_lock = asyncio.Lock()
        self._pending_edit_task: asyncio.Task[None] | None = None
        self._closed: bool = False

    @property
    def message(self) -> Any | None:
        return self._message

    async def start(self) -> None:
        """Post the initial placeholder to Discord."""
        if self._message is not None:
            return
        initial_content = f"{self._header}\n*thinking...* ✍️"
        try:
            self._message = await self._send_initial(initial_content)
            self._last_edit_time = time.monotonic()
        except Exception:
            logger.warning("failed to send initial stream placeholder", exc_info=True)

    async def on_delta(self, delta: str) -> None:
        """Handle an incoming text delta from the agent CLI."""
        if self._closed or not delta:
            return
        self._accumulated += delta
        now = time.monotonic()
        elapsed = now - self._last_edit_time
        if elapsed >= self._throttle_seconds:
            if self._pending_edit_task is None or self._pending_edit_task.done():
                await self._apply_edit()
        else:
            if self._pending_edit_task is None or self._pending_edit_task.done():
                remaining = self._throttle_seconds - elapsed
                self._pending_edit_task = asyncio.create_task(
                    self._delayed_edit(remaining)
                )

    async def _delayed_edit(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            if not self._closed:
                await self._apply_edit()
        except asyncio.CancelledError:
            pass

    async def _apply_edit(self) -> None:
        if self._message is None or self._closed:
            return
        async with self._edit_lock:
            if self._closed:
                return
            display_text = f"{self._header}\n{self._accumulated} ✍️"
            if len(display_text) > DISCORD_MESSAGE_LIMIT - 10:
                head_len = len(self._header) + 20
                keep_tail = DISCORD_MESSAGE_LIMIT - head_len - 10
                display_text = f"{self._header}\n... {self._accumulated[-keep_tail:]} ✍️"
            try:
                edit_fn = getattr(self._message, "edit", None)
                if edit_fn is not None:
                    try:
                        await edit_fn(
                            content=display_text,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    except TypeError:
                        await edit_fn(content=display_text)
                self._last_edit_time = time.monotonic()
            except Exception:
                logger.debug("transient error editing streaming message", exc_info=True)

    async def finish(self, final_text: str) -> str | None:
        """Finalize the message with the complete, clean response text."""
        self._closed = True
        if self._pending_edit_task is not None and not self._pending_edit_task.done():
            self._pending_edit_task.cancel()
            try:
                await self._pending_edit_task
            except asyncio.CancelledError:
                pass

        chunks = split_for_discord(final_text)
        if not chunks:
            return None

        async with self._edit_lock:
            if self._message is not None:
                try:
                    edit_fn = getattr(self._message, "edit", None)
                    if edit_fn is not None:
                        try:
                            await edit_fn(
                                content=chunks[0],
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                        except TypeError:
                            await edit_fn(content=chunks[0])
                except Exception:
                    logger.warning(
                        "failed to edit message to final content", exc_info=True
                    )

                if len(chunks) > 1 and self._send_followup is not None:
                    for chunk in chunks[1:]:
                        try:
                            await self._send_followup(chunk)
                        except Exception:
                            logger.warning(
                                "failed to send follow-up chunk", exc_info=True
                            )

                msg_id = getattr(self._message, "id", None)
                return str(msg_id) if msg_id is not None else None

            # Fallback if start() failed: send chunks via send_initial
            sent_id = None
            try:
                first = await self._send_initial(chunks[0])
                if first is not None and getattr(first, "id", None):
                    sent_id = str(first.id)
                if len(chunks) > 1 and self._send_followup is not None:
                    for chunk in chunks[1:]:
                        await self._send_followup(chunk)
            except Exception:
                logger.warning("failed to send fallback final message", exc_info=True)
            return sent_id

    async def abort(
        self, notice: str = "The agent turn could not be completed."
    ) -> None:
        """Cancel streaming and replace placeholder with a notice."""
        self._closed = True
        if self._pending_edit_task is not None and not self._pending_edit_task.done():
            self._pending_edit_task.cancel()
            try:
                await self._pending_edit_task
            except asyncio.CancelledError:
                pass

        if self._message is not None:
            async with self._edit_lock:
                try:
                    edit_fn = getattr(self._message, "edit", None)
                    if edit_fn is not None:
                        content = f"{self._header}\n*{notice}*"
                        try:
                            await edit_fn(
                                content=content,
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                        except TypeError:
                            await edit_fn(content=content)
                except Exception:
                    logger.debug("failed to abort stream message", exc_info=True)
