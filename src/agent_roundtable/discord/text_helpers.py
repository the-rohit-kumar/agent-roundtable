"""Input text handling and friend-safe status construction (§28.3)."""

from __future__ import annotations

import re
import unicodedata

# Bounds from §28.3 (topic bounded and sanitized; messages bounded reasonably)
TOPIC_MAX_LENGTH = 200
MESSAGE_MAX_LENGTH = 2000

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_RENDERER_CHARS = frozenset({"\u200b", "\u200c", "\u200d", "\ufeff", "\u2060"})


def sanitize_text(raw: str, max_length: int) -> str:
    """Normalize/CRLF/control-char clean, strip zero-width render tricks."""
    text = unicodedata.normalize("NFC", raw.replace("\r\n", "\n").replace("\r", "\n"))
    text = _CONTROL_CHARS.sub("", text)
    text = "".join(ch for ch in text if ch not in _RENDERER_CHARS)
    text = text.strip()
    if len(text) > max_length:
        text = text[:max_length] + "…"
    return text


def friend_safe_status(*, discord_connected: bool, discussion_available: bool) -> str:
    """§28.3: friends receive ONLY connectivity + availability. No paths,
    provider quota details, job prompts, or owner tasks."""
    connectivity = "connected" if discord_connected else "connecting/retrying"
    availability = "available" if discussion_available else "temporarily unavailable"
    return (
        f"Status: Discord {connectivity}; discussion service {availability}.\n"
        "For help with what you can do as a guest, use /help."
    )


def owner_status_summary(
    *,
    discord_connected: bool,
    state: str,
    provider_states: dict[str, str],
    active_count: int = 0,
    database_up: bool = True,
    uptime_seconds: int = 0,
    disk_free_gb: float | None = None,
    disk_warning: bool = False,
    interrupted_tasks: int = 0,
    queued_tasks: int = 0,
) -> str:
    """Owner status shows more detail but still nothing secret-like."""
    providers = (
        ", ".join(
            f"{agent}={state_value}"
            for agent, state_value in sorted(provider_states.items())
        )
        or "(no providers registered)"
    )
    disk = (
        "disk space unavailable"
        if disk_free_gb is None
        else f"disk {disk_free_gb:.1f} GiB free"
        + (" (LOW; project writes paused)" if disk_warning else "")
    )
    uptime = f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m"
    return (
        f"State: {state}; Discord {'connected' if discord_connected else 'not connected'}; "
        f"uptime: {uptime}; providers: {providers}; active conversations: {active_count}; "
        f"database: {'healthy' if database_up else 'UNAVAILABLE'}; {disk}; "
        f"tasks: {queued_tasks} queued, {interrupted_tasks} interrupted."
    )


def denied_message(reason_code: str) -> str:
    """User-facing denial text mapped from stable reason codes."""
    if reason_code == "not_member":
        return "You are not registered as an Owner or invited Friend on this server."
    if reason_code == "owner_only_command":
        return "This command is restricted to the server owner."
    if reason_code == "unknown_command":
        return "This command is not available. Use /help for what you can do."
    return "You do not have permission to use this command."
