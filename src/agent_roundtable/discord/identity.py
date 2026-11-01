"""Identity extraction from Discord interactions (duck-typed for tests)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CallerIdentity:
    discord_user_id: str
    display_name: str
    role_ids: list[str]
    channel_id: str | None
    guild_id: str | None


def extract_identity(interaction: object) -> CallerIdentity:
    """Pull user/role/channel identifiers from any supported interaction shape.

    Works with real discord.py interactions (Interaction / ComponentInteraction)
    and with the fake interaction objects used in unit tests.
    """
    user = getattr(interaction, "user", None)
    user_id = str(getattr(user, "id", "0"))
    display_name = str(
        getattr(user, "display_name", None) or getattr(user, "name", "unknown")
    )
    roles: list[str] = []
    if user is not None and hasattr(user, "roles"):
        roles = [str(r.id) if not isinstance(r, str) else r for r in user.roles]
    member = getattr(interaction, "member", None)
    if not roles and member is not None and hasattr(member, "roles"):
        roles = [str(r.id) if not isinstance(r, str) else r for r in member.roles]
    channel_id = getattr(interaction, "channel_id", None)
    if not channel_id:
        channel = getattr(interaction, "channel", None)
        channel_id = getattr(channel, "id", None) if channel is not None else None
    guild_id = getattr(interaction, "guild_id", None)
    return CallerIdentity(
        discord_user_id=user_id,
        display_name=display_name,
        role_ids=roles,
        channel_id=str(channel_id) if channel_id is not None else None,
        guild_id=str(guild_id) if guild_id is not None else None,
    )
