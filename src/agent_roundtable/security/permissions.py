"""Permission service: Owner/Friend role matrix, enforced server-side (§4.2).

Rule (§4.2): "All permission checks must be performed in the controller's command
handlers and policy service. Hiding a slash command in Discord is not sufficient
authorization." Every command handler MUST call `authorize_command`, and every
decision must be auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    FRIEND = "friend"
    NOT_MEMBER = "not_member"


#: Friend-usable commands (discussion-only scope; §4.4, §28.3)
FRIEND_COMMANDS: frozenset[str] = frozenset(
    {"help", "talk", "reply", "thread", "status"}
)

#: Owner-only commands (§28.3). Friends are rejected server-side even if the
#: Discord UI exposes them (e.g. API access without our bot installing intents).
OWNER_COMMANDS: frozenset[str] = frozenset(
    {
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
    }
)

#: Reason codes recorded in the audit log alongside authorization outcomes.
REASON_OK = "ok"
REASON_NOT_MEMBER = "not_member"
REASON_OWNER_ONLY = "owner_only_command"
REASON_UNKNOWN_COMMAND = "unknown_command"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    role: Role
    reason_code: str
    command: str


class PermissionService:
    """Discord-role resolution + command authorization for Roundtable."""

    def __init__(
        self,
        owner_user_ids: list[str],
        friend_role_ids: list[str],
    ) -> None:
        self._owner_ids = {str(uid) for uid in owner_user_ids}
        self._friend_roles = {str(rid) for rid in friend_role_ids}

    def resolve_role(self, user_id: str, member_role_ids: list[str]) -> Role:
        """Owner by explicit user-ID; friend by carrying a configured role."""
        if str(user_id) in self._owner_ids:
            return Role.OWNER
        roles = {str(rid) for rid in member_role_ids}
        if roles & self._friend_roles:
            return Role.FRIEND
        return Role.NOT_MEMBER

    def authorize_command(
        self, user_id: str, member_role_ids: list[str], command: str
    ) -> PolicyDecision:
        """The server-side gate. Commands NOT in either matrix are denied."""
        role = self.resolve_role(user_id, member_role_ids)
        if role is Role.NOT_MEMBER:
            return PolicyDecision(False, role, REASON_NOT_MEMBER, command)
        if command in FRIEND_COMMANDS:
            return PolicyDecision(True, role, REASON_OK, command)
        if command in OWNER_COMMANDS and role is Role.OWNER:
            return PolicyDecision(True, role, REASON_OK, command)
        if command in OWNER_COMMANDS and role is Role.FRIEND:
            return PolicyDecision(False, role, REASON_OWNER_ONLY, command)
        return PolicyDecision(False, role, REASON_UNKNOWN_COMMAND, command)

    def is_owner(self, user_id: str, member_role_ids: list[str]) -> bool:
        return self.resolve_role(user_id, member_role_ids) is Role.OWNER
