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

    async def on_ready(self) -> None:
        logger.info(
            "connected as %s (guild configured: %s)",
            self.user,
            self.config.discord.guild_id,
        )


def _register_commands(bot: RoundtableBot) -> None:
    """Bind handlers to slash commands. Thin adapters over pure handler logic."""

    @bot.tree.command(name="help", description="Show commands available to you")
    async def _help(interaction: discord.Interaction) -> None:
        await handlers.handle_help(interaction, bot.services)

    @bot.tree.command(name="talk", description="Start a shared open discussion")
    @app_commands.describe(topic="What should the discussion be about?")
    async def _talk(interaction: discord.Interaction, topic: str) -> None:
        await handlers.handle_talk(interaction, bot.services, topic)

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

    for name in handlers._OWNER_COMMAND_NAMES:

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
