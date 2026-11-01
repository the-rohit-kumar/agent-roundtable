# BUILD_PLAN.md

Plan of record for Agent Roundtable. Derived from `ARCHITECTURE.md` (v2.0, normative
addendum §§24–39). Milestone ordering and acceptance criteria are the spec's; this
file records the sequence, files, tests, and manual setup for each milestone.
Selected implementation choices live in `DECISIONS.md`. Live verification state
lives in `IMPLEMENTATION_STATUS.md`.

## Locked decisions (cannot be weakened)

1. **Windows startup: user login** — Task Scheduler logon trigger under the normal user account.
2. **Friend permissions: discussion only** — backend authorization in every handler + policy engine.
3. **Code-writing policy: both agents can implement** — each writer in its own worktree; path-scope write locks; overlapping scopes serialize.
4. **Autonomous triggers: free/event-driven** — all supported sources; dedupe/echo/concurrency/quota guardrails are safety layers, not trigger restrictions.
5. **Retention: forever** — no TTL; deletion only via owner `/forget` with backup invalidation and tombstone audit.

## Milestone sequence

```
M0 Environment & CLI capability verification
 └─> M1 Application foundation (repo, config, SQLite, logging, single-instance)
      └─> M2 Discord bot & role enforcement
           └─> M3 CLI adapters & process supervisor
                └─> M4 Shared Roundtable conversations
                     └─> M5 Memory & permanent archive
                          └─> M6 Event-driven autonomy
                               └─> M7 Dual-agent implementation & isolated worktrees
                                    └─> M8 Windows startup, hardening, release
```

Dependency rules:
- `persistence/` (database, migrations, repositories) is the root dependency.
- `security/` policy engine gates Discord commands, event dispatch, and task execution.
- Adapters depend on the process supervisor; the context builder depends only on repositories.
- Event router is never called directly by a Discord handler (ARCHITECTURE §30.5).
- Worktree writes require supervisor + adapters + policy + registry (hence M7).

## Milestone details

### M0 — Environment & CLI capability verification
- **Files**: `docs/environment-report.md`, `scripts/doctor.ps1`, `docs/milestone0/` (captured CLI help/fixtures), parser prototype evidence.
- **Verified (2026-11-01)**: Windows 11 build 26200; Python 3.14.6; Git 2.55.0; Codex CLI 0.166.0 authenticated (ChatGPT mode, restricted sandbox default); **Antigravity `agy` NOT FOUND** → degraded single-agent mode (DECISIONS D5).
- **Exit criteria**: §20 M0; Antigravity recorded `blocked` with exact blocker; Codex fully verified.

### M1 — Application foundation
- **Files**: `pyproject.toml`, `uv.lock`, `src/agent_roundtable/` (config, lifecycle, persistence, observability, domain, cli), `config/config.example.yaml`, `README.md`, unit tests.
- **Tests**: migration from empty DB; determinism; WAL/FK pragmas; restart persistence; config validation & secret-non-disclosure; single-instance mutex.
- **Manual setup**: none.

### M2 — Discord bot & role enforcement
- **Files**: `discord/` (bot, commands, permissions, publishers), `secrets/` Credential Manager backend, mocked-interaction tests.
- **Tests**: permission matrix enforced server-side; friend `/status` leaks nothing; idempotent interactions; message persistence metadata.
- **Manual setup (owner)**: Discord application/bot with `bot` + `applications.commands`; Message Content Intent **off**; token stored via `agent-roundtable secrets set discord-token`; guild/owner/friend-role/channel IDs.

### M3 — CLI adapters & process supervisor
- **Files**: `adapters/` (codex, antigravity, result_parser), `execution/supervisor.py`, `execution/process_tree.py`, fake CLI fixtures, parser tests.
- **Tests**: §35.3 fixture suite; zero-exit-no-final-message; success+soft-denial; `/stop` tree kill; quota/auth pause; no paid fallback.
- **Manual setup**: opt-in live provider contract tests (quota-aware, separated from CI).

### M4 — Shared conversations — `orchestration/*`, `prompts/*.md`; turn policy persisted; disagreement preservation; archive linkage.
### M5 — Memory & archive — `memory/*`, `/memory` `/forget`, backup/restore; partitioning tests inspect real context bundles; no TTL.
### M6 — Event-driven autonomy — `events/*`, dedupe on `(event_type, dedupe_key)`, echo suppression, restart recovery, friend-reach boundary.
### M7 — Worktrees & execution — `projects/*`, `execution/worktree_manager.py|command_policy.py|approvals.py|write_locks.py`; concurrent separate worktrees; pristine checkout; blocked push/merge; owner registers projects via local CLI only.
### M8 — Startup & release — Task Scheduler scripts, recovery, security suite, `README` + `docs/setup.md|security.md|operations.md|troubleshooting.md`; §37 live checklist.

## Stop policy

One milestone at a time; stop after its acceptance criteria pass (or are recorded
blocked) and report exact test commands and outcomes. Never weaken security,
friend permissions, memory isolation, retention, or worktree isolation to pass a test.
