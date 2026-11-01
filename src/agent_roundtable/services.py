"""Composition-root services container (wired in lifecycle, §26.4)."""

from __future__ import annotations

from dataclasses import dataclass

from agent_roundtable.config import AppConfig
from agent_roundtable.lifecycle import AppLifecycle
from agent_roundtable.observability.audit import AuditService
from agent_roundtable.persistence.database import Database
from agent_roundtable.persistence.repositories.audit import AuditRepository
from agent_roundtable.persistence.repositories.conversations import (
    ConversationRepository,
    InteractionRepository,
)
from agent_roundtable.security.permissions import PermissionService


@dataclass
class Services:
    config: AppConfig
    lifecycle: AppLifecycle
    db: Database
    conversations: ConversationRepository
    interactions: InteractionRepository
    permissions: PermissionService
    audit: AuditService

    @classmethod
    def build(cls, config: AppConfig) -> Services:
        lifecycle = AppLifecycle()
        db = Database(config.database_path)
        audit_repo = AuditRepository(db)
        return cls(
            config=config,
            lifecycle=lifecycle,
            db=db,
            conversations=ConversationRepository(db),
            interactions=InteractionRepository(db),
            permissions=PermissionService(
                config.discord.owner_user_ids, config.discord.friend_role_ids
            ),
            audit=AuditService(audit_repo),
        )
