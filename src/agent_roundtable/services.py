"""Composition-root services container (wired in lifecycle, §26.4)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from agent_roundtable.adapters.antigravity import AntigravityAdapter
from agent_roundtable.adapters.codex import CodexAdapter
from agent_roundtable.config import AppConfig
from agent_roundtable.events.router import EventRouter
from agent_roundtable.events.sources import EventSources
from agent_roundtable.execution.supervisor import RunRegistry, Supervisor
from agent_roundtable.execution.task_service import TaskService
from agent_roundtable.execution.worktree_manager import WorktreeManager
from agent_roundtable.execution.write_locks import WriteLockRepository
from agent_roundtable.lifecycle import AppLifecycle
from agent_roundtable.observability.audit import AuditService
from agent_roundtable.orchestration.conversations import ConversationManager
from agent_roundtable.orchestration.prompt_builder import PromptBuilder
from agent_roundtable.persistence.backup import BackupService
from agent_roundtable.persistence.database import Database
from agent_roundtable.persistence.forget import ForgetService
from agent_roundtable.persistence.repositories.approvals import ApprovalRepository
from agent_roundtable.persistence.repositories.audit import AuditRepository
from agent_roundtable.persistence.repositories.conversations import (
    ConversationRepository,
    InteractionRepository,
)
from agent_roundtable.persistence.repositories.decisions import DecisionRepository
from agent_roundtable.persistence.repositories.events import EventRepository
from agent_roundtable.persistence.repositories.memory import MemoryRepository
from agent_roundtable.persistence.repositories.runs import (
    AgentRunRepository,
    ConversationTurnRepository,
)
from agent_roundtable.persistence.repositories.tasks import TaskRepository
from agent_roundtable.projects.registry import ProjectRegistry
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
    conversation_manager: ConversationManager | None = None
    memories: MemoryRepository | None = None
    backups: BackupService | None = None
    forget: ForgetService | None = None
    events: EventRepository | None = None
    event_router: EventRouter | None = None
    event_sources: EventSources | None = None
    projects: ProjectRegistry | None = None
    tasks: TaskRepository | None = None
    approvals: ApprovalRepository | None = None
    task_service: TaskService | None = None
    adapters: dict | None = None

    @classmethod
    def build(cls, config: AppConfig) -> Services:
        lifecycle = AppLifecycle()
        db = Database(config.database_path)
        audit_repo = AuditRepository(db)
        supervisor = Supervisor(
            RunRegistry(
                {
                    "codex": config.autonomy.max_processes_per_agent,
                    "antigravity": config.autonomy.max_processes_per_agent,
                }
            )
        )
        conversations = ConversationRepository(db)
        turns = ConversationTurnRepository(db)
        runs = AgentRunRepository(db)
        decisions = DecisionRepository(db)
        memories = MemoryRepository(db)
        backups = BackupService(db, config.backups_dir)
        events = EventRepository(db)
        projects = ProjectRegistry(
            db, config.resolved_worktree_root, config.resolved_data_dir
        )
        tasks = TaskRepository(db)
        approvals = ApprovalRepository(db)
        locks = WriteLockRepository(db)
        worktrees = WorktreeManager(config.resolved_worktree_root)
        event_router = EventRouter(
            events, config.autonomy.duplicate_event_window_minutes
        )
        adapters = {
            "codex": CodexAdapter(
                supervisor=supervisor,
                executable=config.agents.codex.executable,
                write_verified=config.execution.codex_workspace_write_verified,
                available=config.agents.codex.enabled,
            ),
            "antigravity": AntigravityAdapter(
                supervisor=supervisor,
                executable=config.agents.antigravity.executable,
                available=config.agents.antigravity.enabled,
                sandbox_verified=True,
            ),
        }
        persona_paths = {
            "codex": config.agents.codex.persona_file,
            "antigravity": config.agents.antigravity.persona_file,
        }
        event_sources = EventSources(
            event_router,
            enabled=config.autonomy.supported_triggers_enabled,
            agent_discoveries=config.autonomy.agent_discovery_events,
            agent_follow_ups=config.autonomy.agent_initiated_discussions,
        )

        async def discover_from_response(
            agent_id: str, response: str, conversation: dict
        ) -> None:
            parent_id = conversation.get("trigger_event_id")
            depth = 0
            if parent_id:
                parent = await events.get(parent_id)
                if parent:
                    try:
                        depth = (
                            int(
                                json.loads(parent["payload_json"]).get("chain_depth", 0)
                            )
                            + 1
                        )
                    except (ValueError, TypeError):
                        depth = 3
            if depth >= 3:
                return
            try:
                await event_sources.consume_agent_output(
                    agent_id=agent_id,
                    response=response,
                    conversation_id=conversation.get("conversation_id"),
                    parent_event_id=parent_id,
                    chain_depth=depth,
                )
            except ValueError:
                # Feature flags default off; unconfigured discovery output is inert.
                return

        manager = ConversationManager(
            conversations=conversations,
            turns=turns,
            runs=runs,
            adapters=adapters,
            prompt_builder=PromptBuilder(persona_paths),
            decisions=decisions,
            memories=memories,
            discovery_sink=discover_from_response,
            max_turns_per_cycle=config.autonomy.max_turns_per_discussion_cycle,
        )
        return cls(
            config=config,
            lifecycle=lifecycle,
            db=db,
            conversations=conversations,
            interactions=InteractionRepository(db),
            permissions=PermissionService(
                config.discord.owner_user_ids, config.discord.friend_role_ids
            ),
            audit=AuditService(audit_repo),
            conversation_manager=manager,
            memories=memories,
            backups=backups,
            forget=ForgetService(db, backups),
            events=events,
            event_router=event_router,
            event_sources=event_sources,
            projects=projects,
            tasks=tasks,
            approvals=approvals,
            adapters=adapters,
            task_service=TaskService(
                db=db,
                projects=projects,
                tasks=tasks,
                approvals=approvals,
                locks=locks,
                worktrees=worktrees,
                runs=runs,
                adapters=adapters,
                data_root=config.resolved_data_dir,
                low_disk_space_threshold_gb=config.observability.low_disk_space_threshold_gb,
            ),
        )
