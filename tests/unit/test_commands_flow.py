"""End-to-end command flow tests with fake Discord interactions (§28.3).

Fake objects duck-type the pieces handlers actually touch:
``interaction.response.defer``, ``interaction.followup.send`` (returns an object
with ``.id``), and ``interaction.id`` / ``.user`` / ``.channel_id`` / guild id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from agent_roundtable.config import (
    AgentProfileSettings,
    AgentsConfig,
    AppConfig,
    DiscordConfig,
)
from agent_roundtable.discord import commands
from agent_roundtable.lifecycle import AppLifecycle, AppState
from agent_roundtable.observability.audit import AuditService
from agent_roundtable.persistence.database import Database
from agent_roundtable.persistence.repositories.audit import AuditRepository
from agent_roundtable.persistence.repositories.conversations import (
    ConversationRepository,
    InteractionRepository,
)
from agent_roundtable.persistence.repositories.tasks import TaskRepository
from agent_roundtable.projects.registry import ProjectRegistry
from agent_roundtable.security.permissions import PermissionService
from agent_roundtable.services import Services

OWNER_ID = "100000000000000001"
FRIEND_ID = "555000000000000555"
FRIEND_ROLE_ID = "200000000000000002"


class FakeUser:
    def __init__(self, user_id: str, role_ids: list[str] | None = None) -> None:
        self.id = int(user_id)
        self.display_name = "tester"
        self.roles: list[Any] = []
        for role_id in role_ids or []:

            class _Role:
                pass

            role = _Role()
            role.id = int(role_id)
            self.roles.append(role)


class FakeResponse:
    def __init__(self, interaction: FakeInteraction) -> None:
        self._interaction = interaction
        self.deferred_ephemeral: bool | None = None

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.deferred_ephemeral = ephemeral


class FakeFollowup:
    def __init__(self, interaction: FakeInteraction) -> None:
        self._interaction = interaction
        self.messages: list[tuple[str, bool]] = []
        self.threads: list[Any] = []
        self._counter = 0

    async def send(self, *, content: str, ephemeral: bool = False) -> Any:
        self._counter += 1
        self.messages.append((content, ephemeral))

        class _Sent:
            pass

        sent = _Sent()
        sent.id = f"m-{self._interaction.id}-{self._counter}"

        async def create_thread(*, name: str, auto_archive_duration: int) -> Any:
            thread = type("_Thread", (), {})()
            thread.id = f"t{self._counter}"
            thread.name = name
            thread.auto_archive_duration = auto_archive_duration

            async def send(*, content: str, allowed_mentions: Any) -> Any:
                thread.messages.append(content)
                sent_message = type("_ThreadMessage", (), {})()
                sent_message.id = f"{thread.id}-m{len(thread.messages)}"
                return sent_message

            thread.send = send
            thread.messages = []
            self.threads.append(thread)
            return thread

        sent.create_thread = create_thread
        return sent


class FakeInteraction:
    def __init__(
        self,
        user_id: str,
        role_ids: list[str] | None = None,
        channel_id: str = "456",
        guild_id: str = "789",
        interaction_id: str | None = None,
    ) -> None:
        self.id = interaction_id or str(uuid4())
        self.user = FakeUser(user_id, role_ids)
        self.channel_id = channel_id
        self.guild_id = guild_id
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)

    @property
    def messages(self) -> list[tuple[str, bool]]:
        return self.followup.messages


async def build_services(tmp_db: Any) -> Services:
    """Real repositories against a temporary database (no Discord network)."""
    db = Database(tmp_db)
    await db.initialize()
    profile = AgentProfileSettings(executable="codex", persona_file="p.md")
    config = AppConfig(
        agents=AgentsConfig(codex=profile, antigravity=profile),
        discord=DiscordConfig(
            owner_user_ids=[OWNER_ID],
            friend_role_ids=[FRIEND_ROLE_ID],
        ),
    )
    lifecycle = AppLifecycle()
    lifecycle.transition(AppState.READY)
    data_root = Path(tmp_db).parent.parent
    worktree_root = data_root / "worktrees"
    worktree_root.mkdir(parents=True, exist_ok=True)
    return Services(
        config=config,
        lifecycle=lifecycle,
        db=db,
        conversations=ConversationRepository(db),
        interactions=InteractionRepository(db),
        permissions=PermissionService(
            config.discord.owner_user_ids, config.discord.friend_role_ids
        ),
        audit=AuditService(AuditRepository(db)),
        projects=ProjectRegistry(db, worktree_root=worktree_root, data_root=data_root),
        tasks=TaskRepository(db),
    )


async def _recent_audit(services: Services, limit: int = 10) -> list[dict[str, Any]]:
    return list(
        reversed(
            await services.db.fetch_all(
                "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        )
    )


# --- friend /talk ----------------------------------------------------------


async def test_friend_talk_creates_shared_conversation_without_project(tmp_db) -> None:
    services = await build_services(tmp_db)
    interaction = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_talk(interaction, services, "Testing infrastructure")
    rows = await services.db.fetch_all("SELECT * FROM conversations")
    assert len(rows) == 1
    row = rows[0]
    assert row["project_id"] is None  # §25.3: friends carry no project
    assert row["visibility"] == "shared"
    assert row["origin"] == "friend"
    assert row["created_by_discord_id"] == FRIEND_ID
    assert row["discord_thread_id"] == "t1"
    assert interaction.messages[0][1] is False  # not ephemeral
    messages = await services.db.fetch_all("SELECT * FROM messages")
    assert any("[topic] Testing infrastructure" in m["content"] for m in messages)


async def test_friend_talk_denied_for_not_member(tmp_db) -> None:
    services = await build_services(tmp_db)
    interaction = FakeInteraction("1337133713370000", role_ids=[])
    await commands.handle_talk(interaction, services, "hi")
    assert await services.db.fetch_all("SELECT * FROM conversations") == []
    assert interaction.messages, "denial must be surfaced to the user"
    content, ephemeral = interaction.messages[0]
    assert "not registered" in content.lower()
    assert ephemeral is True
    audit_rows = await _recent_audit(services)
    assert any(
        r["result"] == "denied" and r["reason_code"] == "not_member" for r in audit_rows
    )


async def test_friend_cannot_select_structured_talk_mode(tmp_db) -> None:
    services = await build_services(tmp_db)
    interaction = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_talk(interaction, services, "A debate topic", mode="debate")
    assert await services.db.fetch_all("SELECT * FROM conversations") == []
    assert interaction.messages[0][1] is True
    audit_rows = await _recent_audit(services)
    assert any(
        row["action"] == "command.talk.structured_mode"
        and row["result"] == "denied"
        and row["reason_code"] == "owner_only_mode"
        for row in audit_rows
    )


async def test_owner_structured_mode_is_owner_only_and_not_public(tmp_db) -> None:
    services = await build_services(tmp_db)
    interaction = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_talk(
        interaction, services, "A decision topic", mode="decision"
    )
    row = (await services.db.fetch_all("SELECT * FROM conversations"))[0]
    assert row["mode"] == "decision"
    assert row["visibility"] == "owner_only"
    assert all(ephemeral for _, ephemeral in interaction.messages)


async def test_agent_reply_is_published_in_persisted_discord_thread(tmp_db) -> None:
    services = await build_services(tmp_db)

    class _Manager:
        async def process_user_turn(self, conversation_id, *, trigger_reason, publish):
            await publish("Agent answer in the conversation")

    services.conversation_manager = _Manager()  # type: ignore[assignment]
    interaction = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_talk(interaction, services, "Threaded discussion")
    row = (await services.db.fetch_all("SELECT * FROM conversations"))[0]
    assert row["discord_thread_id"] == "t1"
    assert interaction.followup.threads[0].messages == [
        "Agent answer in the conversation"
    ]


# --- owner-command denial --------------------------------------------------


@pytest.mark.parametrize("command", sorted(commands._OWNER_COMMAND_NAMES))
async def test_friend_denied_every_owner_command_and_audited(
    tmp_db, command: str
) -> None:
    services = await build_services(tmp_db)
    interaction = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_owner_placeholder(interaction, services, command)
    content, ephemeral = interaction.messages[0]
    assert ephemeral is True
    assert "restricted to the server owner" in content.lower()
    audit_rows = await _recent_audit(services)
    assert any(
        r["action"] == f"command.{command}"
        and r["result"] == "denied"
        and r["reason_code"] == "owner_only_command"
        for r in audit_rows
    ), f"owner denial for /{command} must be audited"


async def test_owner_gets_placeholder_acknowledgement(tmp_db) -> None:
    services = await build_services(tmp_db)
    interaction = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_owner_placeholder(interaction, services, "memory")
    content, ephemeral = interaction.messages[0]
    assert ephemeral is False
    assert "not active yet" in content


# --- /status --------------------------------------------------------------


async def test_friend_status_leaks_nothing_sensitive(tmp_db) -> None:
    services = await build_services(tmp_db)
    interaction = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_status(interaction, services)
    content, _ = interaction.messages[0]
    lowered = content.lower()
    for forbidden in ("c:\\", "worktree", "quota", "codex", "persona", "config"):
        assert forbidden.lower() not in lowered, forbidden
    assert "connected" in lowered


async def test_owner_status_shows_state(tmp_db) -> None:
    services = await build_services(tmp_db)
    interaction = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_status(interaction, services)
    content, _ = interaction.messages[0]
    assert "State: ready" in content
    assert str(tmp_db) not in content  # even owners get no raw db path


# --- duplicate interactions ------------------------------------------------


async def test_duplicate_talk_interaction_is_idempotent(tmp_db) -> None:
    services = await build_services(tmp_db)
    interaction = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID], interaction_id="dup-1")
    await commands.handle_talk(interaction, services, "Topic")
    # Redelivery of the SAME interaction id (Discord retries):
    repeat = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID], interaction_id="dup-1")
    await commands.handle_talk(repeat, services, "Topic")
    rows = await services.db.fetch_all("SELECT * FROM conversations")
    assert len(rows) == 1  # second delivery must not create another conversation
    assert any("Already processing" in content for content, _ in repeat.messages)


# --- /reply ---------------------------------------------------------------


async def test_reply_persists_message_and_updates_status(tmp_db) -> None:
    services = await build_services(tmp_db)
    topic_interaction = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_talk(topic_interaction, services, "topic")
    conversation_id = (await services.db.fetch_all("SELECT * FROM conversations"))[0][
        "conversation_id"
    ]
    reply = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_reply(reply, services, conversation_id, "My friend reply")
    messages = await services.db.fetch_all(
        "SELECT * FROM messages WHERE author_type = 'friend' ORDER BY created_at"
    )
    assert any(m["content"] == "My friend reply" for m in messages)
    row = await services.db.fetch_one("SELECT * FROM conversations")
    assert row["status"] == "waiting_for_agent"


async def test_reply_rejected_for_owner_only_conversation(tmp_db) -> None:
    services = await build_services(tmp_db)
    owner_topic = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_talk(owner_topic, services, "owner private topic")
    conversation_id = (await services.db.fetch_all("SELECT * FROM conversations"))[0][
        "conversation_id"
    ]
    assert (await services.db.fetch_all("SELECT * FROM conversations"))[0][
        "visibility"
    ] == "owner_only"
    friend_reply = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_reply(friend_reply, services, conversation_id, "sneak in")
    messages = await services.db.fetch_all(
        "SELECT * FROM messages WHERE author_type = 'friend'"
    )
    assert messages == []
    audit_rows = await _recent_audit(services)
    assert any(
        r["result"] == "denied" and r["reason_code"] == "owner_only_conversation"
        for r in audit_rows
    )


async def test_reply_to_unknown_conversation_reports_errors(tmp_db) -> None:
    services = await build_services(tmp_db)
    friend_reply = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_reply(friend_reply, services, str(uuid4()), "hi")
    assert "unknown conversation" in friend_reply.messages[0][0].lower()


# --- /thread --------------------------------------------------------------


async def test_thread_metadata_only_for_visible_conversation(tmp_db) -> None:
    services = await build_services(tmp_db)
    topic_interaction = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_talk(topic_interaction, services, "topic")
    conversation_id = (await services.db.fetch_all("SELECT * FROM conversations"))[0][
        "conversation_id"
    ]
    viewer = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_thread(viewer, services, conversation_id)
    content, _ = viewer.messages[0]
    assert "mode:" in content and "status:" in content
    assert "\\" not in content and "C:" not in content


async def test_thread_locked_for_owner_only_conversation(tmp_db) -> None:
    services = await build_services(tmp_db)
    owner_topic = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_talk(owner_topic, services, "private")
    conversation_id = (await services.db.fetch_all("SELECT * FROM conversations"))[0][
        "conversation_id"
    ]
    viewer = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_thread(viewer, services, conversation_id)
    content, _ = viewer.messages[0]
    assert "non-visible" in content.lower() or "unknown" in content.lower()


async def test_thread_for_owner_visible_on_own_conversation(tmp_db) -> None:
    services = await build_services(tmp_db)
    owner_topic = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_talk(owner_topic, services, "private")
    conversation_id = (await services.db.fetch_all("SELECT * FROM conversations"))[0][
        "conversation_id"
    ]
    viewer = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_thread(viewer, services, conversation_id)
    assert "mode:" in viewer.messages[0][0]


# --- /help ----------------------------------------------------------------


async def test_help_lists_friend_commands_for_friend(tmp_db) -> None:
    services = await build_services(tmp_db)
    interaction = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_help(interaction, services)
    content, _ = interaction.messages[0]
    assert "/talk" in content and "/reply" in content
    assert "not available to Friends" in content


# --- owner slash commands --------------------------------------------------


async def test_owner_debate_and_research_commands(tmp_db) -> None:
    services = await build_services(tmp_db)
    owner = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_debate(owner, services, "Static vs Dynamic Typing")
    rows = await services.db.fetch_all(
        "SELECT * FROM conversations WHERE mode='debate'"
    )
    assert len(rows) == 1
    assert rows[0]["visibility"] == "owner_only"

    owner2 = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_research(owner2, services, "How consensus works")
    rows2 = await services.db.fetch_all(
        "SELECT * FROM conversations WHERE mode='research'"
    )
    assert len(rows2) == 1


async def test_friend_denied_debate_and_research(tmp_db) -> None:
    services = await build_services(tmp_db)
    friend = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_debate(friend, services, "Forbidden debate")
    assert friend.messages[0][1] is True
    assert "restricted to the server owner" in friend.messages[0][0].lower()

    friend2 = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_research(friend2, services, "Forbidden research")
    assert friend2.messages[0][1] is True
    assert "restricted to the server owner" in friend2.messages[0][0].lower()


async def test_owner_projects_and_lifecycle_commands(tmp_db) -> None:
    services = await build_services(tmp_db)
    owner = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_projects(owner, services, action="list")
    assert "No projects registered yet" in owner.messages[0][0]

    # test friend denied projects
    friend = FakeInteraction(FRIEND_ID, [FRIEND_ROLE_ID])
    await commands.handle_projects(friend, services, action="list")
    assert "restricted to the server owner" in friend.messages[0][0].lower()

    # test stop and resume
    owner_conv = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_talk(owner_conv, services, "Stopper")
    conv_row = (await services.db.fetch_all("SELECT * FROM conversations"))[-1]
    cid = conv_row["conversation_id"]

    stop_it = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_stop(stop_it, services, cid)
    assert "stopped" in stop_it.messages[0][0].lower()

    resume_it = FakeInteraction(OWNER_ID, role_ids=[])
    await commands.handle_resume(resume_it, services, cid)
    assert "resumed" in resume_it.messages[0][0].lower()
