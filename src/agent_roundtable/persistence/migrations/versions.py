"""Migration 0001: initial logical schema (ARCHITECTURE §29.1, all required tables).

UUID text primary keys, UTC ISO-8601 timestamps, foreign keys on high-frequency
relations, unique dedupe constraint on events, and the §29.2 indexes.

Note (§29.3): this file must never be edited after release. Future schema changes
are added as new migrations in this package.
"""

MIGRATION_0001 = """
CREATE TABLE agents (
    agent_id      TEXT PRIMARY KEY,
    provider      TEXT UNIQUE NOT NULL,
    display_name  TEXT NOT NULL,
    persona_path  TEXT NOT NULL,
    enabled       INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE projects (
    project_id      TEXT PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    repo_path       TEXT UNIQUE NOT NULL,
    default_branch  TEXT NOT NULL,
    base_visibility TEXT NOT NULL,
    policy_json     TEXT NOT NULL,
    enabled         INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE conversations (
    conversation_id       TEXT PRIMARY KEY,
    project_id            TEXT NULL REFERENCES projects(project_id),
    mode                  TEXT NOT NULL,
    visibility            TEXT NOT NULL,
    origin                TEXT NOT NULL,
    status                TEXT NOT NULL,
    discord_channel_id    TEXT,
    discord_thread_id     TEXT,
    created_by_discord_id TEXT,
    trigger_event_id      TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    closed_at             TEXT
);

CREATE TABLE messages (
    message_id         TEXT PRIMARY KEY,
    conversation_id    TEXT NOT NULL REFERENCES conversations(conversation_id),
    author_type        TEXT NOT NULL,
    author_id          TEXT NOT NULL,
    content            TEXT NOT NULL,
    visibility         TEXT NOT NULL,
    discord_message_id TEXT UNIQUE,
    provider_run_id    TEXT,
    reply_to_message_id TEXT,
    created_at         TEXT NOT NULL,
    metadata_json      TEXT NOT NULL
);

CREATE TABLE agent_runs (
    run_id              TEXT PRIMARY KEY,
    agent_id            TEXT NOT NULL,
    conversation_id     TEXT,
    task_id             TEXT,
    purpose             TEXT NOT NULL,
    status              TEXT NOT NULL,
    provider_session_id TEXT,
    working_directory   TEXT,
    permission_profile  TEXT NOT NULL,
    exit_code           INTEGER,
    provider_status     TEXT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    duration_ms         INTEGER,
    usage_json          TEXT NOT NULL,
    diagnostics_json    TEXT NOT NULL,
    stdout_artifact_id  TEXT,
    stderr_artifact_id  TEXT,
    correlation_id      TEXT NOT NULL
);

CREATE TABLE events (
    event_id        TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    source          TEXT NOT NULL,
    project_id      TEXT,
    conversation_id TEXT,
    dedupe_key      TEXT NOT NULL,
    visibility      TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    available_at    TEXT NOT NULL,
    processed_at    TEXT,
    failure_code    TEXT,
    UNIQUE(event_type, dedupe_key)
);

CREATE TABLE tasks (
    task_id          TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL REFERENCES projects(project_id),
    conversation_id  TEXT,
    created_by       TEXT NOT NULL,
    source           TEXT NOT NULL,
    title            TEXT NOT NULL,
    goal             TEXT NOT NULL,
    acceptance_json  TEXT NOT NULL,
    path_scope_json  TEXT NOT NULL,
    risk_level       TEXT NOT NULL,
    preferred_writer TEXT,
    status           TEXT NOT NULL,
    base_commit      TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    completed_at     TEXT
);

CREATE TABLE task_runs (
    task_run_id TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(task_id),
    agent_id    TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    role        TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    summary     TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE worktrees (
    worktree_id TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(task_id),
    agent_id    TEXT NOT NULL,
    path        TEXT UNIQUE NOT NULL,
    branch      TEXT UNIQUE NOT NULL,
    base_commit TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    removed_at  TEXT,
    final_commit TEXT,
    diff_hash   TEXT
);

CREATE TABLE write_locks (
    lock_id     TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(project_id),
    task_id     TEXT NOT NULL,
    path_prefix TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    released_at TEXT,
    status      TEXT NOT NULL
);

CREATE TABLE approvals (
    approval_id     TEXT PRIMARY KEY,
    action_type     TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL,
    requested_by    TEXT NOT NULL,
    decided_by      TEXT,
    decision_reason TEXT,
    created_at      TEXT NOT NULL,
    decided_at      TEXT,
    expires_at      TEXT
);

CREATE TABLE memory_items (
    memory_id            TEXT PRIMARY KEY,
    scope                TEXT NOT NULL CHECK (scope IN ('private', 'shared')),
    agent_id             TEXT,
    project_id           TEXT,
    category             TEXT NOT NULL,
    content              TEXT NOT NULL,
    status               TEXT NOT NULL,
    confidence           TEXT NOT NULL,
    source_refs_json     TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    approved_by          TEXT,
    approved_at          TEXT,
    supersedes_memory_id TEXT,
    CHECK ( (scope = 'private' AND agent_id IS NOT NULL)
         OR (scope = 'shared'  AND agent_id IS NULL) )
);

CREATE TABLE decisions (
    decision_id       TEXT PRIMARY KEY,
    project_id        TEXT,
    conversation_id   TEXT NOT NULL,
    title             TEXT NOT NULL,
    status            TEXT NOT NULL,
    decision_text     TEXT NOT NULL,
    alternatives_json TEXT NOT NULL,
    evidence_json     TEXT NOT NULL,
    dissent_json      TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    approved_by       TEXT,
    approved_at       TEXT
);

CREATE TABLE disagreements (
    disagreement_id       TEXT PRIMARY KEY,
    conversation_id       TEXT NOT NULL,
    topic                 TEXT NOT NULL,
    positions_json        TEXT NOT NULL,
    evidence_json         TEXT NOT NULL,
    status                TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    resolved_at           TEXT,
    resolution_decision_id TEXT
);

CREATE TABLE artifacts (
    artifact_id   TEXT PRIMARY KEY,
    project_id    TEXT,
    task_id       TEXT,
    run_id        TEXT,
    artifact_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    visibility    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE usage_records (
    usage_id       TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL,
    provider       TEXT NOT NULL,
    usage_json     TEXT NOT NULL,
    estimated_cost TEXT,
    recorded_at    TEXT NOT NULL
);

CREATE TABLE audit_events (
    audit_id       TEXT PRIMARY KEY,
    actor_type     TEXT NOT NULL,
    actor_id       TEXT NOT NULL,
    action         TEXT NOT NULL,
    target_type    TEXT,
    target_id      TEXT,
    result         TEXT NOT NULL,
    reason_code    TEXT,
    correlation_id TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    details_json   TEXT NOT NULL
);

CREATE TABLE discord_interactions (
    interaction_id  TEXT PRIMARY KEY,
    discord_user_id TEXT NOT NULL,
    command_name    TEXT NOT NULL,
    status          TEXT NOT NULL,
    conversation_id TEXT,
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);

CREATE TABLE backup_runs (
    backup_id    TEXT PRIMARY KEY,
    destination  TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    completed_at TEXT,
    metadata_json TEXT NOT NULL
);

CREATE INDEX idx_conversations_project_created ON conversations (project_id, created_at);
CREATE INDEX idx_messages_conversation_created ON messages (conversation_id, created_at);
CREATE INDEX idx_agent_runs_agent_status       ON agent_runs (agent_id, status);
CREATE INDEX idx_events_status_available       ON events (status, available_at);
CREATE INDEX idx_events_project                ON events (project_id);
CREATE INDEX idx_tasks_project_status          ON tasks (project_id, status);
CREATE INDEX idx_task_runs_task_status         ON task_runs (task_id, status);
CREATE INDEX idx_worktrees_task                ON worktrees (task_id);
CREATE INDEX idx_audit_created                 ON audit_events (created_at);
CREATE INDEX idx_memory_scope                  ON memory_items (scope, project_id);
"""

MIGRATION_0002 = """
CREATE TABLE conversation_turns (
    turn_id         TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    turn_index      INTEGER NOT NULL,
    agent_id        TEXT NOT NULL,
    run_id          TEXT NOT NULL REFERENCES agent_runs(run_id),
    trigger_reason  TEXT NOT NULL,
    decision        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (conversation_id, turn_index)
);

CREATE INDEX idx_conversation_turns_order
    ON conversation_turns (conversation_id, turn_index);
"""

MIGRATION_0003 = """
ALTER TABLE memory_items ADD COLUMN created_by TEXT NOT NULL DEFAULT 'system';
CREATE TABLE forget_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX idx_forget_confirmations_target
    ON forget_confirmations (actor_id, target_type, target_id, expires_at);
"""

MIGRATION_0004 = """
ALTER TABLE tasks ADD COLUMN validation_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE tasks ADD COLUMN commit_sha TEXT;
ALTER TABLE tasks ADD COLUMN failure_code TEXT;
"""

MIGRATION_0005 = """
CREATE TABLE controller_state (
    state_id TEXT PRIMARY KEY CHECK (state_id='singleton'),
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

MIGRATIONS = [
    (1, "initial_schema", MIGRATION_0001),
    (2, "conversation_turns", MIGRATION_0002),
    (3, "memory_forget_confirmations", MIGRATION_0003),
    (4, "task_validation_evidence", MIGRATION_0004),
    (5, "controller_restart_tracking", MIGRATION_0005),
]
