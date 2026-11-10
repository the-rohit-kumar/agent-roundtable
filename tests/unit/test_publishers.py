"""Discord publishing helpers: splitting, fences, visibility routing (§28.4)."""

from __future__ import annotations

from agent_roundtable.config import (
    AgentProfileSettings,
    AgentsConfig,
    AppConfig,
    DiscordChannelsConfig,
    DiscordConfig,
)
from agent_roundtable.discord.publishers import (
    DISCORD_MESSAGE_LIMIT,
    DiscordStreamSession,
    artifact_reference_text,
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
from agent_roundtable.domain.models import ConversationVisibility

DISCORD_FIELDS = {
    "executable": "codex",
    "persona_file": "p.md",
}


def make_config(channels: DiscordChannelsConfig) -> AppConfig:
    return AppConfig(
        agents=AgentsConfig(
            codex=AgentProfileSettings(**DISCORD_FIELDS),
            antigravity=AgentProfileSettings(**DISCORD_FIELDS),
        ),
        discord=DiscordConfig(channels=channels),
    )


def test_short_content_not_split() -> None:
    assert split_for_discord("hello") == ["hello"]


def test_long_paragraphs_split_within_limit() -> None:
    content = "\n\n".join("x" * 500 for _ in range(6))
    chunks = split_for_discord(content)
    assert len(chunks) >= 2
    assert all(len(chunk) <= DISCORD_MESSAGE_LIMIT for chunk in chunks)
    assert "".join(chunks).count("x") == content.count("x")


def test_oversized_single_word_hard_cut() -> None:
    chunks = split_for_discord("z" * (DISCORD_MESSAGE_LIMIT + 10), limit=50)
    assert all(len(chunk) <= 50 for chunk in chunks)
    assert "".join(chunks) == "z" * (DISCORD_MESSAGE_LIMIT + 10)


def test_odd_fence_count_is_balanced_per_chunk() -> None:
    content = "\n\n".join("```\ncode block\n" for _ in range(20))
    chunks = split_for_discord(content, limit=60)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.count("```") % 2 == 0, chunk


def test_resolve_output_channel_shared_goes_to_open_room() -> None:
    config = make_config(
        DiscordChannelsConfig(
            open_room="chan-open",
            owner_operations="chan-own",
            agent_activity="chan-agent",
        )
    )
    assert resolve_output_channel(config, ConversationVisibility.SHARED) == "chan-open"


def test_resolve_output_channel_owner_prefers_owner_operations() -> None:
    config = make_config(
        DiscordChannelsConfig(
            open_room="chan-open",
            owner_operations="chan-own",
            agent_activity="chan-agent",
        )
    )
    result = resolve_output_channel(config, ConversationVisibility.OWNER_ONLY)
    assert result == "chan-own"
    fallback_config = make_config(
        DiscordChannelsConfig(open_room="chan-open", agent_activity="chan-agent")
    )
    assert (
        resolve_output_channel(fallback_config, ConversationVisibility.OWNER_ONLY)
        == "chan-agent"
    )


def test_artifact_reference_never_leaks_path() -> None:
    text = artifact_reference_text("C:\\Users\\owner\\proj\\artifacts\\notes.md")
    assert "\\" not in text
    assert "C:" not in text
    assert "notes.md" in text


def test_sanitize_strips_control_and_zero_width_chars() -> None:
    dirty = "hi\u200bthere\x00\u0007roboto"
    clean = sanitize_text(dirty, 100)
    assert "\u200b" not in clean
    assert "\x00" not in clean
    assert clean == "hithereroboto"


def test_sanitize_truncates_with_ellipsis() -> None:
    clean = sanitize_text("a" * 500, TOPIC_MAX_LENGTH)
    assert len(clean) == TOPIC_MAX_LENGTH + 1
    assert clean.endswith("…")


def test_sanitize_message_bound() -> None:
    clean = sanitize_text("w" * (MESSAGE_MAX_LENGTH + 50), MESSAGE_MAX_LENGTH)
    assert len(clean) == MESSAGE_MAX_LENGTH + 1


def test_friend_safe_status_has_no_paths_or_quota() -> None:
    text = friend_safe_status(discord_connected=True, discussion_available=True)
    for forbidden in ("\\\\", "C:", "quota", "token", "path"):
        assert forbidden.lower() not in text.lower(), forbidden


def test_owner_status_summary_lists_providers() -> None:
    text = owner_status_summary(
        discord_connected=True,
        state="ready",
        provider_states={"codex": "READY"},
    )
    assert "codex=READY" in text
    assert "ready" in text


def test_owner_status_reports_disk_and_interrupted_work_without_affecting_friend_view() -> (
    None
):
    owner = owner_status_summary(
        discord_connected=False,
        state="degraded",
        provider_states={"codex": "QUOTA_BLOCKED"},
        uptime_seconds=3661,
        disk_free_gb=1.2,
        disk_warning=True,
        interrupted_tasks=2,
        queued_tasks=1,
    )
    assert "QUOTA_BLOCKED" in owner
    assert "LOW" in owner
    assert "1h 1m" in owner
    assert "2 interrupted" in owner
    friend = friend_safe_status(discord_connected=False, discussion_available=False)
    for private_detail in ("QUOTA", "disk", "interrupted", "C:\\"):
        assert private_detail not in friend


def test_denied_message_mapping() -> None:
    assert "owner" in denied_message("owner_only_command").lower()
    assert "not registered" in denied_message("not_member").lower()
    assert "/help" in denied_message("unknown_command")
    assert denied_message("mystery")  # fallback never empty


class _FakeDiscordMessage:
    def __init__(self, message_id: str, content: str) -> None:
        self.id = message_id
        self.content = content
        self.edits: list[str] = []

    async def edit(self, *, content: str, **kwargs: object) -> None:
        self.content = content
        self.edits.append(content)


import pytest


@pytest.mark.asyncio
async def test_stream_session_lifecycle() -> None:
    messages: list[_FakeDiscordMessage] = []

    async def send_msg(content: str) -> _FakeDiscordMessage:
        msg = _FakeDiscordMessage(f"msg_{len(messages) + 1}", content)
        messages.append(msg)
        return msg

    header = "**Codex · Open Room · turn 1**"
    session = DiscordStreamSession(
        send_initial=send_msg,
        header=header,
        throttle_seconds=0.01,
        send_followup=send_msg,
    )

    await session.start()
    assert len(messages) == 1
    assert "*thinking...* ✍️" in messages[0].content

    await session.on_delta("Hello ")
    await session.on_delta("world!")
    import asyncio

    await asyncio.sleep(0.05)  # allow throttled edit to fire

    assert len(messages[0].edits) >= 1
    assert "Hello world! ✍️" in messages[0].content

    final_id = await session.finish(f"{header}\nHello world!")
    assert final_id == "msg_1"
    assert messages[0].content == f"{header}\nHello world!"
    assert "✍️" not in messages[0].content


@pytest.mark.asyncio
async def test_stream_session_oversized_splitting() -> None:
    messages: list[_FakeDiscordMessage] = []

    async def send_msg(content: str) -> _FakeDiscordMessage:
        msg = _FakeDiscordMessage(f"msg_{len(messages) + 1}", content)
        messages.append(msg)
        return msg

    header = "**Codex · Open Room · turn 1**"
    session = DiscordStreamSession(
        send_initial=send_msg,
        header=header,
        throttle_seconds=0.01,
        send_followup=send_msg,
    )

    await session.start()
    # 3000 chars of text
    big_text = f"{header}\n" + ("Paragraph " * 300)
    final_id = await session.finish(big_text)

    assert final_id == "msg_1"
    assert len(messages) >= 2
    assert all(len(m.content) <= DISCORD_MESSAGE_LIMIT for m in messages)


@pytest.mark.asyncio
async def test_stream_session_abort() -> None:
    messages: list[_FakeDiscordMessage] = []

    async def send_msg(content: str) -> _FakeDiscordMessage:
        msg = _FakeDiscordMessage(f"msg_{len(messages) + 1}", content)
        messages.append(msg)
        return msg

    header = "**Codex · Open Room · turn 1**"
    session = DiscordStreamSession(
        send_initial=send_msg,
        header=header,
        throttle_seconds=0.01,
        send_followup=send_msg,
    )

    await session.start()
    await session.abort("Could not complete response.")

    assert "*Could not complete response.*" in messages[0].content
