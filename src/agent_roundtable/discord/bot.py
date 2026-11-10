"""Discord gateway bot: connects, registers slash commands, wires services.

Construction is separated from network I/O so handlers can be unit-tested with
fake interaction objects. Real guild smoke tests run via `agent-roundtable run`.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from agent_roundtable.config import AppConfig
from agent_roundtable.discord import commands as handlers
from agent_roundtable.domain.models import ConversationMode
from agent_roundtable.lifecycle import AppState
from agent_roundtable.secrets.store import SecretStore
from agent_roundtable.services import Services

logger = logging.getLogger("agent_roundtable.discord")


class RoundtableBot(discord.Client):
    def __init__(self, config: AppConfig, services: Services) -> None:
        intents = discord.Intents.none()
        # Application duties do not need message content or presence (§25):
        intents.guilds = True
        intents.guild_messages = True
        super().__init__(intents=intents)
        self.config = config
        self.services = services
        self.tree = app_commands.CommandTree(self)
        self._event_worker: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        _register_commands(self)
        guild_id = self.config.discord.guild_id
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("application commands synced to guild %s", guild_id)
        else:
            await self.tree.sync()
            logger.info("application commands synced globally (no guild configured)")
        if self.services.events is not None:
            recovered = await self.services.events.recover_processing()
            if recovered:
                logger.info("recovered %s interrupted event(s)", recovered)
        if self.config.autonomy.supported_triggers_enabled:
            self._event_worker = asyncio.create_task(self._event_worker_loop())

    async def close(self) -> None:
        if self._event_worker is not None:
            self._event_worker.cancel()
            await asyncio.gather(self._event_worker, return_exceptions=True)
            self._event_worker = None
        self.services.lifecycle.discord_connected = False
        state = self.services.lifecycle.state
        if state not in {AppState.STOPPING, AppState.STOPPED, AppState.FATAL}:
            self.services.lifecycle.transition(AppState.STOPPING)
        try:
            await super().close()
        finally:
            if self.services.lifecycle.state in {AppState.STOPPING, AppState.FATAL}:
                self.services.lifecycle.transition(AppState.STOPPED)

    async def _event_worker_loop(self) -> None:
        while not self.is_closed():
            try:
                if self.services.event_router is not None:
                    await self.services.event_router.drain(self._dispatch_event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("event worker cycle failed")
            await asyncio.sleep(1)

    async def _dispatch_event(self, event: dict) -> None:
        from uuid import UUID

        from agent_roundtable.domain.models import (
            AuthorType,
            ConversationCreate,
            ConversationMode,
            ConversationOrigin,
            ConversationVisibility,
            MessageCreate,
        )

        manager = self.services.conversation_manager
        if manager is None:
            raise RuntimeError("conversation manager unavailable")
        payload = event["payload"]
        conversation_id = (
            UUID(event["conversation_id"]) if event.get("conversation_id") else None
        )
        project_id = UUID(event["project_id"]) if event.get("project_id") else None
        visibility = ConversationVisibility(event["visibility"])
        if conversation_id is None:
            if project_id is not None:
                project = await self.services.db.fetch_one(
                    "SELECT enabled FROM projects WHERE project_id=?",
                    (str(project_id),),
                )
                if project is None or not project["enabled"]:
                    raise ValueError("event project is not registered and enabled")
            conversation = await self.services.conversations.create(
                ConversationCreate(
                    project_id=project_id,
                    mode=ConversationMode.RESEARCH
                    if event["event_type"].value
                    in {
                        "git.commit",
                        "git.changed_paths",
                        "test.failed",
                        "test.completed",
                    }
                    else ConversationMode.OPEN_ROOM,
                    visibility=visibility,
                    origin=ConversationOrigin.EVENT_SOURCE,
                    trigger_event_id=UUID(event["event_id"]),
                )
            )
            conversation_id = conversation.conversation_id
            evidence = ", ".join(payload.evidence_refs)
            await self.services.conversations.append_message(
                MessageCreate(
                    conversation_id=conversation_id,
                    author_type=AuthorType.SYSTEM,
                    author_id=f"event:{event['source']}",
                    content=f"[Untrusted event: {event['event_type'].value}] {payload.title or 'Update'}\n{payload.summary}\nEvidence references: {evidence or '(none)'}",
                    visibility=visibility,
                    metadata_json=f'{{"event_id":"{event["event_id"]}"}}',
                )
            )
        elif event["event_type"].value != "discord.command":
            evidence = ", ".join(payload.evidence_refs)
            await self.services.conversations.append_message(
                MessageCreate(
                    conversation_id=conversation_id,
                    author_type=AuthorType.SYSTEM,
                    author_id=f"event:{event['source']}",
                    content=f"[Untrusted event: {event['event_type'].value}] {payload.title or 'Update'}\n{payload.summary}\nEvidence references: {evidence or '(none)'}",
                    visibility=visibility,
                    metadata_json=f'{{"event_id":"{event["event_id"]}"}}',
                )
            )
            await self.services.conversations.set_trigger_event(
                conversation_id, UUID(event["event_id"])
            )
        channels = self.config.discord.channels
        channel_id = (
            channels.owner_operations
            if visibility is ConversationVisibility.OWNER_ONLY
            else channels.open_room
        )
        channel = self.get_channel(int(channel_id)) if channel_id else None
        if channel is None and channel_id:
            try:
                channel = await self.fetch_channel(int(channel_id))
            except (discord.Forbidden, discord.HTTPException, ValueError):
                logger.warning("configured event output channel unavailable")

        async def publish(content: str) -> str | None:
            if channel is None or not hasattr(channel, "send"):
                return None
            message = await channel.send(
                content, allowed_mentions=discord.AllowedMentions.none()
            )
            return str(message.id) if getattr(message, "id", None) else None

        await manager.process_user_turn(
            conversation_id,
            trigger_reason=f"event:{event['event_type'].value}",
            publish=publish if channel is not None else None,
        )

    async def on_ready(self) -> None:
        self.services.lifecycle.discord_connected = True
        if self.services.lifecycle.state in {AppState.STARTING, AppState.DEGRADED}:
            self.services.lifecycle.transition(AppState.READY)
        logger.info(
            "connected as %s (guild configured: %s)",
            self.user,
            self.config.discord.guild_id,
        )

    async def on_disconnect(self) -> None:
        self.services.lifecycle.discord_connected = False
        if self.services.lifecycle.state is AppState.READY:
            self.services.lifecycle.transition(AppState.DEGRADED)
        logger.warning(
            "Discord gateway disconnected; reconnecting while the host is online"
        )


def _register_commands(bot: RoundtableBot) -> None:
    """Bind handlers to slash commands. Thin adapters over pure handler logic."""

    @bot.tree.command(name="help", description="Show commands available to you")
    async def _help(interaction: discord.Interaction) -> None:
        await handlers.handle_help(interaction, bot.services)

    @bot.tree.command(name="talk", description="Start a shared open discussion")
    @app_commands.describe(
        topic="What should the discussion be about?",
        mode="Structured modes are owner-only; Open Room is available to Friends.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(
                name=mode.value.replace("_", " ").title(), value=mode.value
            )
            for mode in ConversationMode
        ]
    )
    async def _talk(
        interaction: discord.Interaction,
        topic: str,
        mode: str = ConversationMode.OPEN_ROOM.value,
    ) -> None:
        await handlers.handle_talk(interaction, bot.services, topic, mode)

    @bot.tree.command(
        name="reply", description="Add a message to an existing discussion"
    )
    @app_commands.describe(conversation="Discussion id", message="Your message")
    async def _reply(
        interaction: discord.Interaction, conversation: str, message: str
    ) -> None:
        await handlers.handle_reply(interaction, bot.services, conversation, message)

    @bot.tree.command(
        name="thread", description="Inspect a discussion's public metadata"
    )
    @app_commands.describe(conversation="Discussion id")
    async def _thread(interaction: discord.Interaction, conversation: str) -> None:
        await handlers.handle_thread(interaction, bot.services, conversation)

    @bot.tree.command(
        name="status", description="Controller status (limited for Friends)"
    )
    async def _status(interaction: discord.Interaction) -> None:
        await handlers.handle_status(interaction, bot.services)

    @bot.tree.command(name="memory", description="List or approve saved memory")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="List", value="list"),
            app_commands.Choice(name="Approve shared proposal", value="approve"),
        ]
    )
    async def _memory(
        interaction: discord.Interaction, action: str, memory_id: str | None = None
    ) -> None:
        await handlers.handle_memory(interaction, bot.services, action, memory_id)

    @bot.tree.command(
        name="forget", description="Preview and confirm deletion of a memory item"
    )
    async def _forget(
        interaction: discord.Interaction,
        memory_id: str,
        confirmation_token: str | None = None,
    ) -> None:
        await handlers.handle_forget(
            interaction, bot.services, memory_id, confirmation_token
        )

    @bot.tree.command(
        name="debate", description="Start a structured multi-agent debate"
    )
    @app_commands.describe(topic="Debate topic or proposition")
    async def _debate(interaction: discord.Interaction, topic: str) -> None:
        await handlers.handle_debate(interaction, bot.services, topic)

    @bot.tree.command(
        name="research", description="Start an in-depth research investigation"
    )
    @app_commands.describe(question="Research question or topic")
    async def _research(interaction: discord.Interaction, question: str) -> None:
        await handlers.handle_research(interaction, bot.services, question)

    @bot.tree.command(
        name="projects", description="List, inspect, or register approved projects"
    )
    @app_commands.describe(
        action="Action to perform",
        name="Project name (for status/add)",
        path="Local git repository path (for add)",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="List projects", value="list"),
            app_commands.Choice(name="Check status", value="status"),
            app_commands.Choice(name="Add project", value="add"),
        ]
    )
    async def _projects(
        interaction: discord.Interaction,
        action: str = "list",
        name: str | None = None,
        path: str | None = None,
    ) -> None:
        await handlers.handle_projects(interaction, bot.services, action, name, path)

    @bot.tree.command(
        name="implement", description="Create and run an isolated coding task"
    )
    @app_commands.describe(
        project="Registered project name",
        task="Task goal and requirements",
        writer="AI agent to write code",
        path_scope="Optional subdirectory path scope (e.g. src/)",
    )
    @app_commands.choices(
        writer=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="Codex", value="codex"),
            app_commands.Choice(name="Antigravity", value="antigravity"),
        ]
    )
    async def _implement(
        interaction: discord.Interaction,
        project: str,
        task: str,
        writer: str = "auto",
        path_scope: str | None = None,
    ) -> None:
        await handlers.handle_implement(
            interaction, bot.services, project, task, writer, path_scope
        )

    @bot.tree.command(
        name="review", description="Request independent code review of a task"
    )
    @app_commands.describe(
        task_id="Task ID to review",
        reviewer="AI agent to perform review (defaults to independent peer)",
    )
    @app_commands.choices(
        reviewer=[
            app_commands.Choice(name="Auto (Independent Peer)", value="auto"),
            app_commands.Choice(name="Codex", value="codex"),
            app_commands.Choice(name="Antigravity", value="antigravity"),
        ]
    )
    async def _review(
        interaction: discord.Interaction,
        task_id: str,
        reviewer: str = "auto",
    ) -> None:
        await handlers.handle_review(interaction, bot.services, task_id, reviewer)

    @bot.tree.command(name="approve", description="Approve a pending task or action")
    @app_commands.describe(target_id="Task ID or Approval ID to approve")
    async def _approve(interaction: discord.Interaction, target_id: str) -> None:
        await handlers.handle_approve(interaction, bot.services, target_id)

    @bot.tree.command(name="reject", description="Reject a pending task or action")
    @app_commands.describe(
        target_id="Task ID or Approval ID to reject",
        reason="Reason for rejection",
    )
    async def _reject(
        interaction: discord.Interaction,
        target_id: str,
        reason: str = "Rejected by owner",
    ) -> None:
        await handlers.handle_reject(interaction, bot.services, target_id, reason)

    @bot.tree.command(name="stop", description="Stop an active discussion or task")
    @app_commands.describe(target="Conversation ID or Task ID to stop")
    async def _stop(interaction: discord.Interaction, target: str) -> None:
        await handlers.handle_stop(interaction, bot.services, target)

    @bot.tree.command(name="resume", description="Resume a stopped discussion or task")
    @app_commands.describe(target="Conversation ID or Task ID to resume")
    async def _resume(interaction: discord.Interaction, target: str) -> None:
        await handlers.handle_resume(interaction, bot.services, target)

    HANDLED_OWNER_COMMANDS = {
        "memory",
        "forget",
        "debate",
        "research",
        "projects",
        "implement",
        "review",
        "approve",
        "reject",
        "stop",
        "resume",
    }
    for name in handlers._OWNER_COMMAND_NAMES:
        if name in HANDLED_OWNER_COMMANDS:
            continue

        async def _placeholder(
            interaction: discord.Interaction, command_name: str = name
        ) -> None:
            await handlers.handle_owner_placeholder(
                interaction, bot.services, command_name
            )

        bot.tree.command(name=name, description="Owner-only capability")(_placeholder)


def read_discord_token(config: AppConfig, store: SecretStore) -> str | None:
    token = store.get(config.discord.credential_name)
    if token is None:
        logger.error(
            "Discord token missing (credential '%s'); run: agent-roundtable secrets set discord-token",
            config.discord.credential_name,
        )
    return token


async def run_controller(
    config: AppConfig, services: Services, store: SecretStore
) -> int:
    """Main controller loop: connect and run until stopped (§26.3)."""
    token = read_discord_token(config, store)
    if token is None:
        return 3
    bot = RoundtableBot(config, services)
    try:
        await bot.login(token)
        await bot.connect()
    finally:
        try:
            await bot.close()
        except Exception:
            logger.debug("bot close after connect raised", exc_info=True)
    return 0


def run_controller_sync(
    config: AppConfig, services: Services, store: SecretStore
) -> int:
    return asyncio.run(run_controller(config, services, store))


def any_pending_cleanup() -> None:
    """Hook point for shutdown reconciliation (population in M8)."""
    return
