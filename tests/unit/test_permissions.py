"""Permission matrix tests (§4.2, §28.3).

The authorization backend is the source of truth: every owner command must be
denied to friends (server-side), every friend command allowed to both roles,
and non-members denied everything.
"""

from __future__ import annotations

from agent_roundtable.security.permissions import (
    FRIEND_COMMANDS,
    OWNER_COMMANDS,
    REASON_NOT_MEMBER,
    REASON_OK,
    REASON_OWNER_ONLY,
    REASON_UNKNOWN_COMMAND,
    PermissionService,
    Role,
)

OWNER_ID = "100000000000000001"
FRIEND_ROLE_ID = "200000000000000002"
OTHER_ROLE_ID = "300000000000000003"


def make_service() -> PermissionService:
    return PermissionService(
        owner_user_ids=[OWNER_ID], friend_role_ids=[FRIEND_ROLE_ID]
    )


def test_every_owner_command_is_denied_to_friends() -> None:
    """Enumerate the full owner matrix — a future command added to the matrix
    automatically gets this coverage."""
    service = make_service()
    for command in sorted(OWNER_COMMANDS):
        decision = service.authorize_command("999888777666", [FRIEND_ROLE_ID], command)
        assert not decision.allowed, f"friend must be denied /{command}"
        assert decision.role is Role.FRIEND
        assert decision.reason_code == REASON_OWNER_ONLY


def test_every_owner_command_is_allowed_to_owner() -> None:
    service = make_service()
    for command in sorted(OWNER_COMMANDS):
        decision = service.authorize_command(OWNER_ID, [], command)
        assert decision.allowed
        assert decision.reason_code == REASON_OK


def test_every_friend_command_allowed_to_friend_and_owner() -> None:
    service = make_service()
    for command in sorted(FRIEND_COMMANDS):
        friend_decision = service.authorize_command(
            "999888777666", [FRIEND_ROLE_ID], command
        )
        assert friend_decision.allowed
        owner_decision = service.authorize_command(OWNER_ID, [], command)
        assert owner_decision.allowed


def test_owner_can_also_use_friend_commands() -> None:
    service = make_service()
    for command in sorted(FRIEND_COMMANDS):
        decision = service.authorize_command(OWNER_ID, [OTHER_ROLE_ID], command)
        assert decision.allowed


def test_not_member_denied_everywhere() -> None:
    service = make_service()
    all_commands = sorted(FRIEND_COMMANDS | OWNER_COMMANDS) + ["extraneous"]
    for command in all_commands:
        decision = service.authorize_command("424242", [OTHER_ROLE_ID], command)
        assert not decision.allowed
        assert decision.role is Role.NOT_MEMBER
        assert decision.reason_code == REASON_NOT_MEMBER


def test_unknown_command_denied_even_for_owner() -> None:
    service = make_service()
    decision = service.authorize_command(OWNER_ID, [], "definitely_not_a_command")
    assert not decision.allowed
    assert decision.reason_code == REASON_UNKNOWN_COMMAND


def test_role_resolution_prioritizes_owner_over_friend_role() -> None:
    service = make_service()
    assert service.resolve_role(OWNER_ID, [FRIEND_ROLE_ID]) is Role.OWNER


def test_friend_role_membership_by_any_matching_role() -> None:
    service = make_service()
    assert service.resolve_role("555", [OTHER_ROLE_ID, FRIEND_ROLE_ID]) is Role.FRIEND


def test_is_owner_helper() -> None:
    service = make_service()
    assert service.is_owner(OWNER_ID, [])
    assert not service.is_owner("555", [FRIEND_ROLE_ID])
