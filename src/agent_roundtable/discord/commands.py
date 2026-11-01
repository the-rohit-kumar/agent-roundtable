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

import logging
import uuid
from typing import Any

from agent_roundtable.discord.identity import CallerIdentity, extract_identity
from agent_roundtable.discord.publishers import split_for_discord
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
    ConversationOrigin,
    ConversationStatus,
    ConversationVisibility,
    MessageCreate,
)
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


async def _ack(interaction: Any) -> None:
    response = interaction.response
    if hasattr(response, "defer"):
        await response.defer(ephemeral=False)


async def _send(interaction: Any, output: CommandOutput) -> str | None:
    followup = interaction.followup
    if hasattr(followup, "send"):
        result = await followup.send(content=output.content, ephemeral=output.ephemeral)
        return (
            str(result.id)
            if result is not None and getattr(result, "id", None)
            else None
        )
    return None


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
        f"mode: {row['mode']}; status: {row['status']}; messages visible: shared channel only."
    )


# --------------------------------------------------------------------------- #
# Friend-safe commands
# --------------------------------------------------------------------------- #


async def handle_help(interaction: Any, services: Services) -> None:
    _, decision, fresh = await _begin_interaction(interaction, services, "help")
    await _ack(interaction)
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
            "/memory, /forget, /settings"
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


async def handle_talk(interaction: Any, services: Services, topic: str) -> None:
    identity, decision, fresh = await _begin_interaction(interaction, services, "talk")
    await _ack(interaction)
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

    is_owner = decision.role is Role.OWNER
    conversation = await services.conversations.create(
        ConversationCreate(
            project_id=None,  # §25.3: friends never carry a project; owner /talk is general too
            visibility=ConversationVisibility.OWNER_ONLY
            if is_owner
            else ConversationVisibility.SHARED,
            origin=ConversationOrigin.OWNER if is_owner else ConversationOrigin.FRIEND,
            created_by_discord_id=identity.discord_user_id,
            discord_channel_id=identity.channel_id,
        )
    )
    await services.conversations.append_message(
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
        "Agents will respond in this channel. Use /reply to add your message."
    )
    published_ids = []
    for chunk in split_for_discord(intro):
        published = await _send(interaction, CommandOutput(chunk))
        if published:
            published_ids.append(published)
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
    await services.interactions.complete(
        str(getattr(interaction, "id", "unknown")), conversation.conversation_id
    )


async def handle_reply(
    interaction: Any, services: Services, conversation_id: str, message: str
) -> None:
    identity, decision, fresh = await _begin_interaction(interaction, services, "reply")
    await _ack(interaction)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if not fresh:
        await _send(interaction, CommandOutput("🔁 Already processing this request."))
        return

    conversation_id_uuid = _parse_conversation_id(conversation_id)
    if conversation_id_uuid is None:
        await _send(
            interaction, CommandOutput("Unknown conversation id.", ephemeral=True)
        )
        return
    conversation = await services.conversations.get(conversation_id_uuid)
    if conversation is None:
        await _send(
            interaction, CommandOutput("Unknown conversation id.", ephemeral=True)
        )
        return
    visibility = ConversationVisibility(conversation["visibility"])
    if (
        decision.role is not Role.OWNER
        and visibility is not ConversationVisibility.SHARED
    ):
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

    clean_message = sanitize_text(message, MESSAGE_MAX_LENGTH)
    if not clean_message:
        await _send(interaction, CommandOutput("Message was empty.", ephemeral=True))
        return

    is_owner = decision.role is Role.OWNER
    await services.conversations.append_message(
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
    for chunk in split_for_discord(clean_message):
        await _send(interaction, CommandOutput(chunk))
    await services.interactions.complete(
        str(getattr(interaction, "id", "unknown")), conversation_id_uuid
    )


async def handle_thread(
    interaction: Any, services: Services, conversation_id: str
) -> None:
    _, decision, _ = await _begin_interaction(interaction, services, "thread")
    await _ack(interaction)
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
    await _ack(interaction)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    if decision.role is not Role.OWNER:
        content = friend_safe_status(
            discord_connected=bool(identity.guild_id),
            discussion_available=services.lifecycle.state.value
            in ("ready", "starting"),
        )
    else:
        content = owner_status_summary(
            discord_connected=bool(identity.guild_id),
            state=services.lifecycle.state.value,
            provider_states={},
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


async def handle_owner_placeholder(
    interaction: Any, services: Services, command_name: str
) -> None:
    _, decision, _ = await _begin_interaction(interaction, services, command_name)
    await _ack(interaction)
    if not decision.allowed:
        await _send(
            interaction,
            CommandOutput(denied_message(decision.reason_code), ephemeral=True),
        )
        return
    await _send(interaction, CommandOutput(_NOT_YET_IMPLEMENTED_OWNER))
