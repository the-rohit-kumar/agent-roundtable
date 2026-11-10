"""Slash-command handlers (pure testable logic) + registration adapters.

Every handler:
1. records/needs the interaction id for idempotency (§28.3),
2. authorizes via the PermissionService — the backend gate, NOT UI hiding (§4.2),
3. audits the authorization decision (§33.4),
4. acknowledges promptly, then publishes results (§28.3).

Owner-only commands are registered in this milestone but respond with a
"capability arrives in a later milestone" note — future-phase privileged
behavior is NOT implemented early (§36).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from typing import Any
from uuid import UUID

import discord

from agent_roundtable.discord.identity import CallerIdentity, extract_identity
from agent_roundtable.discord.publishers import (
    DiscordStreamSession,
    resolve_output_channel,
    split_for_discord,
)
from agent_roundtable.discord.text_helpers import (
    MESSAGE_MAX_LENGTH,
    TOPIC_MAX_LENGTH,
    denied_message,
    friend_safe_status,
    owner_status_summary,
    sanitize_text,
)
from agent_roundtable.domain.models import (
    AuditResult,
    AuthorType,
    ConversationCreate,
    ConversationMode,
    ConversationOrigin,
    ConversationStatus,
    ConversationVisibility,
    MessageCreate,
)
from agent_roundtable.orchestration.conversations import DISPLAY_NAMES
from agent_roundtable.security.permissions import PolicyDecision, Role
from agent_roundtable.services import Services

logger = logging.getLogger("agent_roundtable.discord")

_NOT_YET_IMPLEMENTED_OWNER = (
    "This capability exists in the architecture but is not active yet; it becomes "
    "available in a later milestone. Authorization was verified and audited."
)


class CommandOutput:
    """Publishing result (discord ids captured where the platform provides them)."""

    def __init__(self, content: str, ephemeral: bool = False) -> None:
        self.content = content
        self.ephemeral = ephemeral


async def _ack(interaction: Any, *, ephemeral: bool = False) -> None:
    response = interaction.response
    if hasattr(response, "defer"):
        await response.defer(ephemeral=ephemeral)


async def _send(interaction: Any, output: CommandOutput) -> str | None:
    result = await _send_result(interaction, output)
    return (
        str(result.id) if result is not None and getattr(result, "id", None) else None
    )


async def _send_result(interaction: Any, output: CommandOutput) -> Any:
    followup = interaction.followup
    if hasattr(followup, "send"):
        kwargs: dict[str, Any] = {
            "content": output.content,
            "ephemeral": output.ephemeral,
        }
        if hasattr(interaction, "client"):
            kwargs["allowed_mentions"] = discord.AllowedMentions.none()
        return await followup.send(**kwargs)
    return None


async def _create_conversation_thread(
    interaction: Any,
    message: Any,
    conversation_id: uuid.UUID,
    *,
    channel_override: Any | None = None,
) -> Any | None:
    if message is None:
        return None
    creator = getattr(message, "create_thread", None)
    channel = channel_override or getattr(interaction, "channel", None)
    try:
        if creator is not None:
            return await creator(
                name=f"roundtable-{str(conversation_id)[:8]}",
                auto_archive_duration=1440,
            )
        channel_creator = getattr(channel, "create_thread", None)
        if channel_creator is not None:
            return await channel_creator(
                name=f"roundtable-{str(conversation_id)[:8]}",
                message=message,
                auto_archive_duration=1440,
            )
    except (discord.Forbidden, discord.HTTPException):
        logger.info("Discord thread creation unavailable; keeping channel conversation")
    return None


async def _send_to_thread(thread: Any, content: str) -> str | None:
    sender = getattr(thread, "send", None)
    if sender is None:
        return None
    message = await sender(
        content=content, allowed_mentions=discord.AllowedMentions.none()
    )
    return (
        str(message.id)
        if message is not None and getattr(message, "id", None)
        else None
    )


async def _resolve_output_channel(
    interaction: Any, services: Services, visibility: ConversationVisibility
) -> Any | None:
    configured_id = resolve_output_channel(services.config, visibility)
    if configured_id is None:
        return None
    channel = getattr(interaction, "channel", None)
    if str(getattr(channel, "id", "")) == str(configured_id):
        return channel
    client = getattr(interaction, "client", None)
    getter = getattr(client, "get_channel", None)
    try:
        resolved = getter(int(configured_id)) if getter is not None else None
    except (TypeError, ValueError):
        logger.warning("configured conversation channel id is invalid")
        return None
    if resolved is not None:
        return resolved
    fetcher = getattr(client, "fetch_channel", None)
    if fetcher is None:
        return None
    try:
        return await fetcher(int(configured_id))
    except (discord.Forbidden, discord.HTTPException):
        logger.warning("configured conversation channel is unavailable")
        return None


async def _send_conversation_message(
    interaction: Any,
    output: CommandOutput,
    *,
    channel: Any | None = None,
) -> Any:
    if channel is not None and hasattr(channel, "send"):
        return await channel.send(
            content=output.content,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    return await _send_result(interaction, output)


async def _authorize(
    interaction: Any, services: Services, command: str
) -> tuple[CallerIdentity, PolicyDecision]:
    identity = extract_identity(interaction)
    decision = services.permissions.authorize_command(
        identity.discord_user_id, identity.role_ids, command
    )
    return identity, decision


async def _begin_interaction(
    interaction: Any, services: Services, command: str
) -> tuple[CallerIdentity, PolicyDecision, bool]:
    """Idempotency + authorization + audit. Returns (identity, decision, fresh)."""
    identity, decision = await _authorize(interaction, services, command)
    fresh = await services.interactions.begin(
        str(getattr(interaction, "id", "unknown")),
        identity.discord_user_id,
        command,
    )
    await services.audit.record(
        actor_type="friend"
        if decision.role is Role.FRIEND
        else ("owner" if decision.role is Role.OWNER else "friend"),
        actor_id=identity.discord_user_id,
        action=f"command.{command}",
        result=AuditResult.SUCCESS if decision.allowed else AuditResult.DENIED,
        reason_code=decision.reason_code,
        details={"guild_id": identity.guild_id, "channel_id": identity.channel_id},
    )
    return identity, decision, fresh


def _parse_conversation_id(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError, TypeError):
        return None


def _conversation_row_to_metadata(row: dict[str, Any]) -> str:
    return (
        f"Conversation `{row['conversation_id']}`\n"
        f"mode: {row['mode']}; status: {row['status']}; "
        f"visibility: {row['visibility']}."
    )


# --------------------------------------------------------------------------- #
# Friend-safe commands
# --------------------------------------------------------------------------- #


async def handle_help(interaction: Any, services: Services) -> None:
    _, decision, fresh = await _begin_interaction(interaction, services, "help")
    await _ack(interaction, ephemeral=not decision.allowed)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if decision.role is Role.OWNER:
        content = (
            "Owner commands: /talk, /reply, /thread, /status, /debate, /research, "
            "/implement, /review, /projects, /approve, /reject, /stop, /resume, "
            "/memory, /forget, /settings. Use /talk mode for Debate, Research, "
            "Brainstorm, Decision, or Code Review."
        )
    else:
        content = (
            "You can use: /talk (start a discussion), /reply (add a message), "
            "/thread (inspect a discussion), /status. Owner-only capabilities are "
            "not available to Friends."
        )
    await _send(interaction, CommandOutput(content))
    if fresh and decision.role is not Role.NOT_MEMBER:
        await _complete_quiet(interaction, services)


async def _complete_quiet(interaction: Any, services: Services) -> None:
    try:
        await services.interactions.complete(str(getattr(interaction, "id", "unknown")))
    except Exception:
        logger.debug("interaction completion bookkeeping failed", exc_info=True)


async def handle_talk(
    interaction: Any,
    services: Services,
    topic: str,
    mode: str = ConversationMode.OPEN_ROOM.value,
    command_name: str = "talk",
) -> None:
    identity, decision, fresh = await _begin_interaction(
        interaction, services, command_name
    )
    await _ack(
        interaction,
        ephemeral=(
            not decision.allowed
            or decision.role is Role.OWNER
            or mode != ConversationMode.OPEN_ROOM.value
        ),
    )
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if not fresh:
        await _send(interaction, CommandOutput("🔁 Already processing this request."))
        return
    clean_topic = sanitize_text(topic, TOPIC_MAX_LENGTH)
    if not clean_topic:
        await _send(
            interaction, CommandOutput("Please provide a topic.", ephemeral=True)
        )
        return

    try:
        conversation_mode = ConversationMode(mode)
    except ValueError:
        await _send(
            interaction,
            CommandOutput("Unknown discussion mode.", ephemeral=True),
        )
        return
    if (
        decision.role is not Role.OWNER
        and conversation_mode is not ConversationMode.OPEN_ROOM
    ):
        await services.audit.record(
            actor_type="friend",
            actor_id=identity.discord_user_id,
            action="command.talk.structured_mode",
            result=AuditResult.DENIED,
            reason_code="owner_only_mode",
        )
        await _send(
            interaction,
            CommandOutput(denied_message("owner_only_command"), ephemeral=True),
        )
        return

    is_owner = decision.role is Role.OWNER
    visibility = (
        ConversationVisibility.OWNER_ONLY if is_owner else ConversationVisibility.SHARED
    )
    output_channel = await _resolve_output_channel(interaction, services, visibility)
    conversation = await services.conversations.create(
        ConversationCreate(
            project_id=None,  # §25.3: friends never carry a project; owner /talk is general too
            mode=conversation_mode,
            visibility=visibility,
            origin=ConversationOrigin.OWNER if is_owner else ConversationOrigin.FRIEND,
            created_by_discord_id=identity.discord_user_id,
            discord_channel_id=str(output_channel.id)
            if output_channel is not None and getattr(output_channel, "id", None)
            else identity.channel_id,
        )
    )
    topic_message = await services.conversations.append_message(
        MessageCreate(
            conversation_id=conversation.conversation_id,
            author_type=AuthorType.OWNER if is_owner else AuthorType.FRIEND,
            author_id=identity.discord_user_id,
            content=f"[topic] {clean_topic}",
            visibility=conversation.visibility,
        )
    )
    intro = (
        f"**Discussion started** `{conversation.conversation_id}`\n"
        f"Topic: {clean_topic}\n"
        "Agents will respond in this conversation. Use /reply to continue."
    )
    published_ids = []
    first_published_message = None
    is_private = conversation.visibility is ConversationVisibility.OWNER_ONLY
    for chunk in split_for_discord(intro):
        published_message = await _send_conversation_message(
            interaction,
            CommandOutput(chunk, ephemeral=is_private and output_channel is None),
            channel=output_channel,
        )
        if first_published_message is None:
            first_published_message = published_message
        published = (
            str(published_message.id)
            if published_message is not None and getattr(published_message, "id", None)
            else None
        )
        if published:
            published_ids.append(published)
    thread = None
    if not is_private or output_channel is not None:
        thread = await _create_conversation_thread(
            interaction,
            first_published_message,
            conversation.conversation_id,
            channel_override=output_channel,
        )
        thread_id = getattr(thread, "id", None) if thread is not None else None
        if thread_id is not None:
            await services.conversations.set_thread(
                conversation.conversation_id, str(thread_id)
            )
    if published_ids:
        await services.conversations.append_message(
            MessageCreate(
                conversation_id=conversation.conversation_id,
                author_type=AuthorType.SYSTEM,
                author_id="controller",
                content=intro,
                visibility=conversation.visibility,
                discord_message_id=published_ids[0],
            )
        )
    if services.conversation_manager is not None:

        async def publish_agent(content: str) -> str | None:
            message_ids: list[str] = []
            for chunk in split_for_discord(content):
                if thread is not None:
                    message_id = await _send_to_thread(thread, chunk)
                else:
                    direct_channel = output_channel
                    published_message = await _send_conversation_message(
                        interaction,
                        CommandOutput(
                            chunk,
                            ephemeral=is_private and direct_channel is None,
                        ),
                        channel=direct_channel,
                    )
                    message_id = (
                        str(published_message.id)
                        if published_message is not None
                        and getattr(published_message, "id", None)
                        else None
                    )
                if message_id:
                    message_ids.append(message_id)
            return message_ids[0] if message_ids else None

        async def stream_factory(
            agent_id: str, turn_index: int, m: ConversationMode
        ) -> DiscordStreamSession:
            label = DISPLAY_NAMES.get(agent_id, agent_id)
            header = (
                f"**{label} · {m.value.replace('_', ' ').title()} · turn {turn_index}**"
            )

            async def _send_chunk(content: str) -> Any:
                if thread is not None:
                    sender = getattr(thread, "send", None)
                    if sender is not None:
                        return await sender(
                            content=content,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                direct_channel = output_channel
                return await _send_conversation_message(
                    interaction,
                    CommandOutput(
                        content,
                        ephemeral=is_private and direct_channel is None,
                    ),
                    channel=direct_channel,
                )

            return DiscordStreamSession(
                send_initial=_send_chunk,
                header=header,
                send_followup=_send_chunk,
            )

        try:

            async def run_event(event: dict[str, Any]) -> None:
                kwargs: dict[str, Any] = {"publish": publish_agent}
                sig = inspect.signature(services.conversation_manager.process_user_turn)
                if "stream_session_factory" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                ):
                    kwargs["stream_session_factory"] = stream_factory

                await services.conversation_manager.process_user_turn(
                    conversation.conversation_id,
                    trigger_reason="user_started_conversation",
                    **kwargs,
                )

            if services.event_sources is not None and services.event_router is not None:
                event, is_new = await services.event_sources.discord_message(
                    user_id=identity.discord_user_id,
                    message_id=str(topic_message.message_id),
                    content=clean_topic,
                    conversation_id=conversation.conversation_id,
                    visibility=conversation.visibility.value,
                )
                if is_new:
                    await services.event_router.dispatch_event(
                        event["event_id"], run_event
                    )
            else:
                await run_event({})
        except Exception:
            logger.exception("conversation turn failed")
            await _send(
                interaction,
                CommandOutput(
                    "The discussion was saved, but the agent turn could not be completed.",
                    ephemeral=is_private,
                ),
            )
    await services.interactions.complete(
        str(getattr(interaction, "id", "unknown")), conversation.conversation_id
    )


async def handle_reply(
    interaction: Any, services: Services, conversation_id: str, message: str
) -> None:
    identity, decision, fresh = await _begin_interaction(interaction, services, "reply")
    if not decision.allowed:
        await _ack(interaction, ephemeral=True)
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if not fresh:
        await _ack(interaction, ephemeral=True)
        await _send(interaction, CommandOutput("🔁 Already processing this request."))
        return

    conversation_id_uuid = _parse_conversation_id(conversation_id)
    if conversation_id_uuid is None:
        await _ack(interaction, ephemeral=True)
        await _send(
            interaction, CommandOutput("Unknown conversation id.", ephemeral=True)
        )
        return
    conversation = await services.conversations.get(conversation_id_uuid)
    if conversation is None:
        await _ack(interaction, ephemeral=True)
        await _send(
            interaction, CommandOutput("Unknown conversation id.", ephemeral=True)
        )
        return
    visibility = ConversationVisibility(conversation["visibility"])
    if (
        decision.role is not Role.OWNER
        and visibility is not ConversationVisibility.SHARED
    ):
        await _ack(interaction, ephemeral=True)
        await services.audit.record(
            actor_type="friend",
            actor_id=identity.discord_user_id,
            action="command.reply",
            target_type="conversation",
            target_id=conversation_id,
            result=AuditResult.DENIED,
            reason_code="owner_only_conversation",
        )
        await _send(
            interaction,
            CommandOutput(denied_message("owner_only_command"), ephemeral=True),
        )
        return
    if conversation["status"] in {
        ConversationStatus.COMPLETED.value,
        ConversationStatus.STOPPED.value,
        ConversationStatus.FAILED.value,
    }:
        await _ack(interaction, ephemeral=True)
        await _send(
            interaction,
            CommandOutput("This discussion is closed.", ephemeral=True),
        )
        return

    clean_message = sanitize_text(message, MESSAGE_MAX_LENGTH)
    if not clean_message:
        await _ack(interaction, ephemeral=True)
        await _send(interaction, CommandOutput("Message was empty.", ephemeral=True))
        return

    is_owner = decision.role is Role.OWNER
    is_private = visibility is ConversationVisibility.OWNER_ONLY
    await _ack(interaction, ephemeral=is_private)
    user_message = await services.conversations.append_message(
        MessageCreate(
            conversation_id=conversation_id_uuid,
            author_type=AuthorType.OWNER if is_owner else AuthorType.FRIEND,
            author_id=identity.discord_user_id,
            content=clean_message,
            visibility=visibility,
        )
    )
    # Keep the original conversation status open/waiting per turn policy (M4).
    await services.conversations.set_status(
        conversation_id_uuid, ConversationStatus.WAITING_FOR_AGENT
    )
    if services.conversation_manager is not None:
        output_channel = await _resolve_output_channel(
            interaction, services, visibility
        )
        target_thread = None
        thread_id = conversation.get("discord_thread_id")
        if thread_id:
            channel = getattr(interaction, "channel", None)
            if str(getattr(channel, "id", "")) == str(thread_id):
                target_thread = channel
            else:
                client = getattr(interaction, "client", None)
                getter = getattr(client, "get_channel", None)
                if getter is not None:
                    target_thread = getter(int(thread_id))
                if target_thread is None:
                    fetcher = getattr(client, "fetch_channel", None)
                    if fetcher is not None:
                        try:
                            target_thread = await fetcher(int(thread_id))
                        except (discord.Forbidden, discord.HTTPException):
                            logger.info("conversation thread is unavailable")

        async def publish_agent(content: str) -> str | None:
            message_ids: list[str] = []
            for chunk in split_for_discord(content):
                if target_thread is not None:
                    message_id = await _send_to_thread(target_thread, chunk)
                elif output_channel is not None:
                    published_message = await _send_conversation_message(
                        interaction,
                        CommandOutput(chunk),
                        channel=output_channel,
                    )
                    message_id = (
                        str(published_message.id)
                        if published_message is not None
                        and getattr(published_message, "id", None)
                        else None
                    )
                else:
                    message_id = await _send(
                        interaction, CommandOutput(chunk, ephemeral=is_private)
                    )
                if message_id:
                    message_ids.append(message_id)
            return message_ids[0] if message_ids else None

        async def stream_factory(
            agent_id: str, turn_index: int, m: ConversationMode
        ) -> DiscordStreamSession:
            label = DISPLAY_NAMES.get(agent_id, agent_id)
            header = (
                f"**{label} · {m.value.replace('_', ' ').title()} · turn {turn_index}**"
            )

            async def _send_chunk(content: str) -> Any:
                if target_thread is not None:
                    sender = getattr(target_thread, "send", None)
                    if sender is not None:
                        return await sender(
                            content=content,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                elif output_channel is not None:
                    return await _send_conversation_message(
                        interaction,
                        CommandOutput(content),
                        channel=output_channel,
                    )
                return await _send_result(
                    interaction, CommandOutput(content, ephemeral=is_private)
                )

            return DiscordStreamSession(
                send_initial=_send_chunk,
                header=header,
                send_followup=_send_chunk,
            )

        try:

            async def run_event(event: dict[str, Any]) -> None:
                kwargs: dict[str, Any] = {"publish": publish_agent}
                sig = inspect.signature(services.conversation_manager.process_user_turn)
                if "stream_session_factory" in sig.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                ):
                    kwargs["stream_session_factory"] = stream_factory

                await services.conversation_manager.process_user_turn(
                    conversation_id_uuid,
                    trigger_reason="user_replied",
                    **kwargs,
                )

            if services.event_sources is not None and services.event_router is not None:
                event, is_new = await services.event_sources.discord_message(
                    user_id=identity.discord_user_id,
                    message_id=str(user_message.message_id),
                    content=clean_message,
                    conversation_id=conversation_id_uuid,
                    visibility=visibility.value,
                )
                if is_new:
                    await services.event_router.dispatch_event(
                        event["event_id"], run_event
                    )
            else:
                await run_event({})
        except Exception:
            logger.exception("conversation turn failed")
            await _send(
                interaction,
                CommandOutput(
                    "Your message was saved, but the agent turn could not be completed.",
                    ephemeral=is_private,
                ),
            )
    else:
        for chunk in split_for_discord(clean_message):
            await _send(interaction, CommandOutput(chunk, ephemeral=is_private))
    await services.interactions.complete(
        str(getattr(interaction, "id", "unknown")), conversation_id_uuid
    )


async def handle_thread(
    interaction: Any, services: Services, conversation_id: str
) -> None:
    _, decision, _ = await _begin_interaction(interaction, services, "thread")
    await _ack(interaction, ephemeral=not decision.allowed)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    try:
        row = await services.conversations.get(uuid.UUID(conversation_id))
    except ValueError:
        row = None
    if row is None or (
        decision.role is not Role.OWNER
        and row["visibility"] != ConversationVisibility.SHARED.value
    ):
        await _send(
            interaction,
            CommandOutput("Unknown or non-visible conversation.", ephemeral=True),
        )
        return
    # §28.3: metadata only — never local paths, prompts, private memory, artifacts.
    await _send(interaction, CommandOutput(_conversation_row_to_metadata(row)))


async def handle_status(interaction: Any, services: Services) -> None:
    identity, decision, _ = await _begin_interaction(interaction, services, "status")
    await _ack(interaction, ephemeral=not decision.allowed)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if decision.role is not Role.OWNER:
        content = friend_safe_status(
            # Receiving this guild interaction confirms the bot is connected now.
            discord_connected=bool(identity.guild_id),
            discussion_available=services.lifecycle.state.value
            in ("ready", "starting"),
        )
    else:
        from agent_roundtable.observability.health import disk_space_status

        database_up = True
        active_count = queued_tasks = interrupted_tasks = 0
        disk_free_gb = None
        disk_warning = False
        try:
            active_row = await services.db.fetch_one(
                "SELECT COUNT(*) AS count FROM conversations WHERE status='active'"
            )
            task_rows = await services.db.fetch_all(
                "SELECT status,COUNT(*) AS count FROM tasks "
                "WHERE status IN ('queued','interrupted') GROUP BY status"
            )
            active_count = int(active_row["count"]) if active_row else 0
            task_counts = {row["status"]: int(row["count"]) for row in task_rows}
            queued_tasks = task_counts.get("queued", 0)
            interrupted_tasks = task_counts.get("interrupted", 0)
        except Exception:
            database_up = False
            logger.exception("owner status health probe failed")
        try:
            disk = disk_space_status(
                services.config.resolved_data_dir,
                services.config.observability.low_disk_space_threshold_gb,
            )
            disk_free_gb, disk_warning = disk.free_gb, disk.warning
        except OSError:
            logger.exception("owner status disk probe failed")
        adapters = services.adapters or {}
        content = owner_status_summary(
            discord_connected=bool(identity.guild_id),
            state=services.lifecycle.state.value,
            provider_states={
                name: adapter.operational_status()
                for name, adapter in adapters.items()
                if hasattr(adapter, "operational_status")
            },
            active_count=active_count,
            database_up=database_up,
            uptime_seconds=services.lifecycle.uptime_seconds,
            disk_free_gb=disk_free_gb,
            disk_warning=disk_warning,
            interrupted_tasks=interrupted_tasks,
            queued_tasks=queued_tasks,
        )
    await _send(interaction, CommandOutput(content))


# --------------------------------------------------------------------------- #
# Owner-only commands (registered now; capability bodies arrive per milestone)
# --------------------------------------------------------------------------- #

_OWNER_COMMAND_NAMES = (
    "debate",
    "research",
    "implement",
    "review",
    "projects",
    "approve",
    "reject",
    "stop",
    "resume",
    "memory",
    "forget",
    "settings",
)


async def handle_memory(
    interaction: Any, services: Services, action: str, memory_id: str | None = None
) -> None:
    _, decision, _ = await _begin_interaction(interaction, services, "memory")
    await _ack(interaction, ephemeral=True)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if services.memories is None:
        result = "Memory service unavailable."
    elif action == "list":
        items = await services.memories.list_items()
        result = (
            "\n".join(
                f"{x['memory_id']} · {x['scope']} · {x['status']} · {x['category']}"
                for x in items
            )
            or "No memory items."
        )
    elif action == "approve" and memory_id:
        approved = await services.memories.approve_shared(
            memory_id, extract_identity(interaction).discord_user_id
        )
        result = (
            "Shared memory approved."
            if approved
            else "No pending shared memory found for that id."
        )
    else:
        result = "Use action list or approve with a memory id."
    await _send(interaction, CommandOutput(result, ephemeral=True))


async def handle_forget(
    interaction: Any,
    services: Services,
    memory_id: str,
    confirmation_token: str | None = None,
) -> None:
    actor, decision, _ = await _begin_interaction(interaction, services, "forget")
    await _ack(interaction, ephemeral=True)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if services.forget is None:
        result = "Forget service unavailable."
    elif confirmation_token:
        removed = await services.forget.confirm(
            actor.discord_user_id, memory_id, confirmation_token
        )
        result = (
            "Memory deleted and managed backups invalidated."
            if removed
            else "Confirmation token is invalid or expired."
        )
    else:
        result = (
            await services.forget.preview(actor.discord_user_id, memory_id)
            or "Memory item not found."
        )
    await _send(interaction, CommandOutput(result, ephemeral=True))


async def handle_debate(
    interaction: Any,
    services: Services,
    topic: str,
) -> None:
    await handle_talk(
        interaction,
        services,
        topic=topic,
        mode=ConversationMode.DEBATE.value,
        command_name="debate",
    )


async def handle_research(
    interaction: Any,
    services: Services,
    question: str,
) -> None:
    await handle_talk(
        interaction,
        services,
        topic=question,
        mode=ConversationMode.RESEARCH.value,
        command_name="research",
    )


async def handle_projects(
    interaction: Any,
    services: Services,
    action: str = "list",
    name: str | None = None,
    path: str | None = None,
) -> None:
    _identity, decision, fresh = await _begin_interaction(
        interaction, services, "projects"
    )
    await _ack(interaction, ephemeral=True)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if not fresh:
        await _send(
            interaction,
            CommandOutput("🔁 Already processing this request.", ephemeral=True),
        )
        return
    if services.projects is None:
        await _send(
            interaction,
            CommandOutput("Project registry service is not available.", ephemeral=True),
        )
        return

    try:
        clean_action = (action or "list").lower()
        if clean_action == "list":
            rows = await services.projects.list()
            if not rows:
                content = (
                    "📂 **No projects registered yet.**\n"
                    "Register a local Git repository using `/projects action:add name:<name> path:<path>` "
                    "or with the CLI: `agent-roundtable project add <name> <path>`."
                )
            else:
                lines = ["📂 **Registered Projects:**"]
                for r in rows:
                    policy = r["policy"]
                    writes_str = (
                        "enabled" if policy.autonomous_write_enabled else "disabled"
                    )
                    lines.append(
                        f"• **{r['name']}** (`{r['project_id']}`)\n"
                        f"  Path: `{r['repo_path']}`\n"
                        f"  Branch: `{r['default_branch']}` · Writes: `{writes_str}`"
                    )
                content = "\n".join(lines)

        elif clean_action in ("status", "doctor"):
            if not name:
                content = "Please provide the project `name` to check status."
            else:
                row = await services.projects.get_by_name(name)
                if row is None:
                    try:
                        row = await services.projects.get(UUID(name))
                    except (ValueError, TypeError):
                        row = None
                if row is None:
                    content = f"Project `{name}` not found."
                else:
                    info = services.projects.inspect_path(row["repo_path"])
                    policy = row["policy"]
                    warn_str = (
                        f"\n  ⚠️ Warnings: {', '.join(info['warnings'])}"
                        if info.get("warnings")
                        else ""
                    )
                    content = (
                        f"📂 **Project Status: {row['name']}**\n"
                        f"  Path: `{info['path']}`\n"
                        f"  Branch: `{info['branch']}` (HEAD: `{info['head'][:10]}`)\n"
                        f"  Autonomous Writes: `{'enabled' if policy.autonomous_write_enabled else 'disabled'}`\n"
                        f"  Allowed Agents: `{', '.join(policy.allowed_agents)}`"
                        f"{warn_str}"
                    )

        elif clean_action == "add":
            if not name or not path:
                content = "To add a project, please provide both `name` and `path`."
            else:
                info = services.projects.inspect_path(path)
                project_id = await services.projects.register(
                    name, info["path"], confirmed=True
                )
                content = (
                    f"✅ **Project registered successfully!**\n"
                    f"Name: **{name}** (`{project_id}`)\n"
                    f"Path: `{info['path']}` (Branch: `{info['branch']}`)\n"
                    f"*(Note: Autonomous writes default to disabled. Configure write policy via CLI when ready.)*"
                )
        else:
            content = f"Unknown action `{action}`. Supported actions: `list`, `status`, `add`."
    except Exception as exc:
        logger.exception("projects command failed")
        content = f"❌ Project action `{action}` failed: {exc}"

    await _send(interaction, CommandOutput(content, ephemeral=True))
    await _complete_quiet(interaction, services)


async def handle_implement(
    interaction: Any,
    services: Services,
    project: str,
    task: str,
    writer: str = "auto",
    path_scope: str | None = None,
) -> None:
    identity, decision, fresh = await _begin_interaction(
        interaction, services, "implement"
    )
    output_channel = await _resolve_output_channel(
        interaction, services, ConversationVisibility.OWNER_ONLY
    )
    is_ephemeral = output_channel is None
    await _ack(interaction, ephemeral=is_ephemeral)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if not fresh:
        await _send(
            interaction,
            CommandOutput("🔁 Already processing this request.", ephemeral=True),
        )
        return
    if (
        services.projects is None
        or services.tasks is None
        or services.task_service is None
    ):
        await _send(
            interaction,
            CommandOutput("Task execution services are not available.", ephemeral=True),
        )
        return

    clean_task = sanitize_text(task, MESSAGE_MAX_LENGTH)
    if not clean_task:
        await _send(
            interaction,
            CommandOutput("Please provide a task description.", ephemeral=True),
        )
        return

    p = await services.projects.get_by_name(project)
    if p is None:
        try:
            p = await services.projects.get(UUID(project))
        except (ValueError, TypeError):
            p = None
    if p is None:
        await _send(
            interaction,
            CommandOutput(
                f"Project `{project}` not found. Use `/projects action:list` to see registered projects.",
                ephemeral=True,
            ),
        )
        return

    policy = p["policy"]
    allowed = policy.allowed_agents
    preferred_writer = writer.lower()
    if preferred_writer not in ("codex", "antigravity"):
        preferred_writer = allowed[0] if allowed else "codex"

    scopes = [path_scope] if path_scope else (policy.allowed_write_roots or ["src/"])

    try:
        task_id = await services.tasks.create(
            project_id=UUID(p["project_id"]),
            created_by=identity.discord_user_id,
            title=clean_task[:200],
            goal=clean_task,
            acceptance=[
                "Code changes must pass project build and tests",
                "Code must pass independent peer review",
            ],
            path_scopes=scopes,
            preferred_writer=preferred_writer,
        )
        await services.task_service.approve(task_id, identity.discord_user_id)

        init_msg = (
            f"🛠️ **Task created & queued** `{task_id}`\n"
            f"Project: **{p['name']}**\n"
            f"Writer: **{DISPLAY_NAMES.get(preferred_writer, preferred_writer)}**\n"
            f"Goal: {clean_task}\n"
            f"Path Scopes: `{', '.join(scopes)}`\n"
            f"*Starting execution in isolated worktree...*"
        )
        await _send_conversation_message(
            interaction,
            CommandOutput(init_msg, ephemeral=is_ephemeral),
            channel=output_channel,
        )

        async def _run_task() -> None:
            try:
                res = await services.task_service.run(task_id, preferred_writer)
                st = res.get("status")
                if st == "committed_local":
                    sha = (res.get("commit_sha") or "")[:10]
                    reviewer = "antigravity" if preferred_writer == "codex" else "codex"
                    done_msg = (
                        f"✅ **Task committed locally** `{task_id}`\n"
                        f"Commit: `{sha}`\n"
                        f"Worktree: `{res.get('worktree')}`\n"
                        f"Next step: Request independent review with `/review task_id:{task_id}` (Reviewer: **{reviewer}**)."
                    )
                elif st == "needs_changes":
                    ev = res.get("evidence", {})
                    fail_code = ev.get("failure_code", "validation_failed")
                    done_msg = (
                        f"⚠️ **Task needs changes** `{task_id}`\n"
                        f"Reason: `{fail_code}`\n"
                        f"Scope violations: {ev.get('scope_violations', [])}"
                    )
                else:
                    done_msg = (
                        f"❌ **Task failed** `{task_id}`: {res.get('reason_code')}"
                    )
                await _send_conversation_message(
                    interaction,
                    CommandOutput(done_msg, ephemeral=is_ephemeral),
                    channel=output_channel,
                )
            except PermissionError as pe:
                hint_flag = (
                    "--enable-writes"
                    if not policy.autonomous_write_enabled
                    else "--allow-write-root <dir> (e.g. backend, frontend)"
                )
                await _send_conversation_message(
                    interaction,
                    CommandOutput(
                        f"⚠️ **Task execution blocked by policy** `{task_id}`: {pe}\n"
                        f"*(Use CLI to adjust policy if needed: `agent-roundtable project policy {p['name']} {hint_flag}`)*",
                        ephemeral=is_ephemeral,
                    ),
                    channel=output_channel,
                )
            except Exception as e:
                logger.exception("task run execution error")
                await _send_conversation_message(
                    interaction,
                    CommandOutput(
                        f"❌ **Task execution error** `{task_id}`: {e}",
                        ephemeral=is_ephemeral,
                    ),
                    channel=output_channel,
                )

        asyncio.create_task(_run_task())

    except Exception as exc:
        logger.exception("failed to create or queue implement task")
        await _send(
            interaction,
            CommandOutput(f"❌ Failed to start implementation: {exc}", ephemeral=True),
        )

    await _complete_quiet(interaction, services)


async def handle_review(
    interaction: Any,
    services: Services,
    task_id: str,
    reviewer: str = "auto",
) -> None:
    _identity, decision, fresh = await _begin_interaction(
        interaction, services, "review"
    )
    output_channel = await _resolve_output_channel(
        interaction, services, ConversationVisibility.OWNER_ONLY
    )
    is_ephemeral = output_channel is None
    await _ack(interaction, ephemeral=is_ephemeral)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if not fresh:
        await _send(
            interaction,
            CommandOutput("🔁 Already processing this request.", ephemeral=True),
        )
        return
    if services.tasks is None or services.task_service is None:
        await _send(
            interaction,
            CommandOutput("Task service unavailable.", ephemeral=True),
        )
        return

    task = await services.tasks.get(task_id)
    if task is None:
        await _send(
            interaction,
            CommandOutput(f"Task `{task_id}` not found.", ephemeral=True),
        )
        return
    if task["status"] != "committed_local":
        await _send(
            interaction,
            CommandOutput(
                f"Task `{task_id}` is in status `{task['status']}`. Only `committed_local` tasks can be reviewed.",
                ephemeral=True,
            ),
        )
        return

    worktree = await services.db.fetch_one(
        "SELECT * FROM worktrees WHERE task_id=? AND status='review' ORDER BY created_at DESC LIMIT 1",
        (task_id,),
    )
    writer = worktree["agent_id"] if worktree else "codex"
    reviewer_id = reviewer.lower()
    if reviewer_id not in ("codex", "antigravity") or reviewer_id == writer:
        reviewer_id = "antigravity" if writer == "codex" else "codex"

    await _send_conversation_message(
        interaction,
        CommandOutput(
            f"🔍 **Starting independent code review for task** `{task_id}`...\n"
            f"Writer: **{DISPLAY_NAMES.get(writer, writer)}**\n"
            f"Reviewer: **{DISPLAY_NAMES.get(reviewer_id, reviewer_id)}**\n"
            f"*Analyzing diff and validating acceptance criteria in read-only sandbox...*",
            ephemeral=is_ephemeral,
        ),
        channel=output_channel,
    )

    try:
        rev_result = await services.task_service.review(task_id, reviewer_id)
        status = rev_result.get("status")
        findings = rev_result.get("response", "")
        if status == "approved":
            msg = (
                f"🎉 **Code Review APPROVED** for task `{task_id}`!\n"
                f"Reviewer: **{DISPLAY_NAMES.get(reviewer_id, reviewer_id)}**\n\n"
                f"**Findings:**\n{findings}\n\n"
                f"Next step: Complete and accept this task with `/approve target_id:{task_id}`."
            )
        elif status == "changes_requested":
            msg = (
                f"⚠️ **Code Review CHANGES REQUESTED** for task `{task_id}`\n"
                f"Reviewer: **{DISPLAY_NAMES.get(reviewer_id, reviewer_id)}**\n\n"
                f"**Findings:**\n{findings}\n\n"
                f"Task transitioned to `needs_changes`. Run `/implement` to address findings."
            )
        else:
            msg = f"❌ **Review failed or inconclusive** for task `{task_id}`.\nFindings:\n{findings}"
        for chunk in split_for_discord(msg):
            await _send_conversation_message(
                interaction,
                CommandOutput(chunk, ephemeral=is_ephemeral),
                channel=output_channel,
            )
    except Exception as e:
        logger.exception("code review execution failed")
        await _send_conversation_message(
            interaction,
            CommandOutput(f"❌ Review execution failed: {e}", ephemeral=is_ephemeral),
            channel=output_channel,
        )

    await _complete_quiet(interaction, services)


async def handle_approve(
    interaction: Any,
    services: Services,
    target_id: str,
) -> None:
    identity, decision, fresh = await _begin_interaction(
        interaction, services, "approve"
    )
    await _ack(interaction, ephemeral=True)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if not fresh:
        await _send(
            interaction,
            CommandOutput("🔁 Already processing this request.", ephemeral=True),
        )
        return

    # Check if target_id is a task
    task = await services.tasks.get(target_id) if services.tasks else None
    if task is not None:
        st = task["status"]
        if st in {"proposed", "awaiting_approval"}:
            if services.task_service is not None:
                await services.task_service.approve(target_id, identity.discord_user_id)
            msg = f"✅ **Task approved & queued for execution:** `{target_id}`"
        elif st == "committed_local":
            if services.task_service is not None:
                try:
                    await services.task_service.complete(
                        target_id, identity.discord_user_id
                    )
                    commit_abbr = (task.get("commit_sha") or "")[:10]
                    msg = (
                        f"🏆 **Task completed and accepted!** `{target_id}`\n"
                        f"The worktree and local commit `{commit_abbr}` are preserved."
                    )
                except PermissionError as pe:
                    msg = f"⚠️ Cannot complete task: {pe}"
            else:
                msg = "Task service unavailable."
        else:
            msg = f"Task `{target_id}` is in status `{st}` and cannot be approved in this state."
        await _send(interaction, CommandOutput(msg, ephemeral=True))
        await _complete_quiet(interaction, services)
        return

    # Otherwise check if target_id is an approval request
    if services.approvals is not None:
        try:
            approval = await services.approvals.get(UUID(target_id))
            if approval:
                await services.approvals.decide(
                    UUID(target_id),
                    identity.discord_user_id,
                    approved=True,
                    reason="Approved by owner via Discord",
                )
                await _send(
                    interaction,
                    CommandOutput(
                        f"✅ **Approval granted** `{target_id}`.", ephemeral=True
                    ),
                )
                await _complete_quiet(interaction, services)
                return
        except (ValueError, TypeError):
            pass

    await _send(
        interaction,
        CommandOutput(
            f"Target `{target_id}` not found among pending tasks or approvals.",
            ephemeral=True,
        ),
    )
    await _complete_quiet(interaction, services)


async def handle_reject(
    interaction: Any,
    services: Services,
    target_id: str,
    reason: str = "Rejected by owner",
) -> None:
    identity, decision, fresh = await _begin_interaction(
        interaction, services, "reject"
    )
    await _ack(interaction, ephemeral=True)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if not fresh:
        await _send(
            interaction,
            CommandOutput("🔁 Already processing this request.", ephemeral=True),
        )
        return

    task = await services.tasks.get(target_id) if services.tasks else None
    if task is not None:
        await services.tasks.transition(target_id, "rejected", failure_code=reason)
        await _send(
            interaction,
            CommandOutput(
                f"🚫 **Task rejected:** `{target_id}`\nReason: {reason}",
                ephemeral=True,
            ),
        )
        await _complete_quiet(interaction, services)
        return

    if services.approvals is not None:
        try:
            approval = await services.approvals.get(UUID(target_id))
            if approval:
                await services.approvals.decide(
                    UUID(target_id),
                    identity.discord_user_id,
                    approved=False,
                    reason=reason,
                )
                await _send(
                    interaction,
                    CommandOutput(
                        f"🚫 **Action rejected:** `{target_id}`\nReason: {reason}",
                        ephemeral=True,
                    ),
                )
                await _complete_quiet(interaction, services)
                return
        except (ValueError, TypeError):
            pass

    await _send(
        interaction,
        CommandOutput(
            f"Target `{target_id}` not found among active tasks or approvals.",
            ephemeral=True,
        ),
    )
    await _complete_quiet(interaction, services)


async def handle_stop(
    interaction: Any,
    services: Services,
    target: str,
) -> None:
    _identity, decision, fresh = await _begin_interaction(interaction, services, "stop")
    await _ack(interaction, ephemeral=True)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if not fresh:
        await _send(
            interaction,
            CommandOutput("🔁 Already processing this request.", ephemeral=True),
        )
        return

    conv_id = _parse_conversation_id(target)
    if conv_id is not None:
        conv = await services.conversations.get(conv_id)
        if conv:
            await services.conversations.set_status(conv_id, ConversationStatus.STOPPED)
            await _send(
                interaction,
                CommandOutput(
                    f"🛑 **Discussion stopped:** `{conv_id}`.", ephemeral=True
                ),
            )
            await _complete_quiet(interaction, services)
            return

    if services.tasks is not None:
        task = await services.tasks.get(target)
        if task:
            await services.tasks.transition(
                target, "canceled", failure_code="stopped_by_owner"
            )
            await _send(
                interaction,
                CommandOutput(
                    f"🛑 **Task stopped/canceled:** `{target}`.", ephemeral=True
                ),
            )
            await _complete_quiet(interaction, services)
            return

    await _send(
        interaction,
        CommandOutput(
            f"Target `{target}` not found as an active conversation or task.",
            ephemeral=True,
        ),
    )
    await _complete_quiet(interaction, services)


async def handle_resume(
    interaction: Any,
    services: Services,
    target: str,
) -> None:
    _identity, decision, fresh = await _begin_interaction(
        interaction, services, "resume"
    )
    await _ack(interaction, ephemeral=True)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if not fresh:
        await _send(
            interaction,
            CommandOutput("🔁 Already processing this request.", ephemeral=True),
        )
        return

    conv_id = _parse_conversation_id(target)
    if conv_id is not None:
        conv = await services.conversations.get(conv_id)
        if conv:
            await services.conversations.set_status(
                conv_id, ConversationStatus.WAITING_FOR_USER
            )
            await _send(
                interaction,
                CommandOutput(
                    f"▶️ **Discussion resumed:** `{conv_id}`.", ephemeral=True
                ),
            )
            await _complete_quiet(interaction, services)
            return

    if services.tasks is not None:
        task = await services.tasks.get(target)
        if task:
            await services.tasks.transition(target, "queued")
            await _send(
                interaction,
                CommandOutput(
                    f"▶️ **Task resumed and re-queued:** `{target}`.", ephemeral=True
                ),
            )
            await _complete_quiet(interaction, services)
            return

    await _send(
        interaction,
        CommandOutput(
            f"Target `{target}` not found as a stopped conversation or task.",
            ephemeral=True,
        ),
    )
    await _complete_quiet(interaction, services)


async def handle_owner_placeholder(
    interaction: Any, services: Services, command_name: str
) -> None:
    _, decision, _ = await _begin_interaction(interaction, services, command_name)
    await _ack(interaction, ephemeral=not decision.allowed)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    await _send(interaction, CommandOutput(_NOT_YET_IMPLEMENTED_OWNER))
