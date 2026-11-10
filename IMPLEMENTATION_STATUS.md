# IMPLEMENTATION_STATUS.md

Status values: `not_started`, `in_progress`, `blocked`, `passed`, `failed`.
A criterion is `passed` only when its test command was executed and its output
captured in the documented environment (ARCHITECTURE §35.5). Last updated: 2026-11-06.

## Milestone 0 — Environment and CLI capability verification — PASSED

| Criterion | Status | Evidence / command |
|---|---|---|
| Windows/Python/Git/CLI versions recorded | passed | `docs/environment-report.md`; Windows build 10.0.26200, Python 3.14.6 (system) / 3.13.14 (uv venv), Git 2.55.0.windows.3, codex-cli 0.166.0, `agy` 1.5.1 |
| Interactive auth possible for both CLIs, no credentials in repo | passed | Codex: `codex login status` → "Logged in using ChatGPT". Antigravity: `agy` 1.5.1 installed, authenticated (Google account), verified 2026-11-06 |
| Each CLI completes a one-shot non-interactive prompt | passed | Codex: live run `codex exec --json --sandbox read-only --ephemeral` → `ROUNDTABLE_HEALTHCHECK_OK`. Antigravity: `agy --print "..."` → `ROUNDTABLE_HEALTHCHECK_OK` (exit 0, 2026-11-06) |
| Both result formats parsed by a small prototype | passed | JSONL event schema captured (`thread.started`, `turn.started/completed`, `item.completed` with `item.type` `agent_message`/`command_execution`, usage in `turn.completed`); parser contract in `codex_cli_surface.md`; M3 parser implements against these fixtures |
| Each provider stops on timeout/cancel | passed (Codex) / internal (no external provider) | Live test with `--no-daemon` long run + 8s timeout → process tree fully terminated, zero leftover processes. Decision D12 recorded: adapter MUST use top-level `--no-daemon` so exec runs are supervisable children |
| Write capability verified only in disposable worktree | passed | Codex: `workspace-write` created `canary.txt` with exact content. Antigravity: `agy --print` → created `canary.txt` with `AGY_WRITE_CANARY_PASS` in disposable git repo (2026-11-06) |
| Antigravity sandbox/denial behavior verified empirically on this machine | passed | `agy -p "..."` in headless mode → soft-denial ("command permission auto-denied") at exit 1; read-only prompt mode → exit 0. Adapter soft-denial handling confirmed correct (2026-11-06) |
| One provider failure doesn't block the other | passed (architectural) | design per-provider states (§26.1); re-verified after M3 contract tests |

**Consequence:** system runs in documented single-agent (Codex-only) degraded mode
until the owner installs and authenticates the Antigravity CLI.## Milestone 1 — Standalone application foundation — PASSED

Evidence commands (all executed this machine, 2026-11-01):
`uv lock` (27 packages resolved) · `uv run pytest -q` → **37 passed** ·
`uv run ruff check src tests` → All checks passed · `uv run ruff format --check` → clean ·
`uv run agent-roundtable --config config/config.example.yaml migrate` → OK ·
`uv run agent-roundtable status` → OK (WAL, foreign_keys ON) ·
`uv run agent-roundtable doctor` → DEGRADED (1 check: `agy` missing — documented)

| Criterion | Status | Evidence |
|---|---|---|
| App starts locally from a documented command | passed | README + verified doctor/status/migrate CLI runs above |
| Database initializes and migrates deterministically | passed | `tests/unit/test_migrations.py::test_initialize_is_deterministic_and_idempotent` live DB verify + `test_migration_checksum_recorded` |
| Database uses WAL + foreign keys | passed | `test_wal_and_foreign_keys_enforced` (live PRAGMA introspection) |
| Restart preserves test records | passed | `test_restart_preserves_records` (close → reopen → same row + checksum) |
| Secrets not committed or printed | passed | `test_config.py` (secret-free config; validation errors don't echo values) + `test_logging_redaction` (token/webhook/bearer patterns redacted) + doctor/status redaction-safe `describe_sanitized()` |
| No dependency on `sleeping-dogs-web` | passed | fresh standalone repo; zero external imports |

Caveat recorded honestly: full-schema initial migration is in place (ALL §29.1
tables + §29.2 indexes); migrations 2+ will carry additive changes if later
milestones require them — the migration framework (version + SHA-256 checksum,
tamper detection, FATAL-on-failure) is tested.

## Milestone 2 — Discord bot and role enforcement — PASSED (mocked tests; real-guild smoke blocked on owner)

Evidence commands (all executed this machine, 2026-11-01):
`uv run pytest -q` → **95 passed** (58 new M2 tests: permission matrix, repos,
publishers, secrets stores, fake-interaction command flows) ·
`uv run ruff check src tests` → All checks passed · `uv run ruff format src tests` → clean ·
`uv run agent-roundtable secrets status discord-token` (with temp config) →
`'AgentRoundtable.DiscordBotToken' missing (value not displayed by design)` —
existence-only display verified ·
Windows Credential Manager backend verified LIVE:
`set('AgentRoundtable.__probe__') → get() == roundtrip True → delete() True → get() is None`

| Criterion | Status | Evidence |
|---|---|---|
| Windows Credential Manager storage, value never displayed | passed | `secrets/store.py` (ctypes advapi32); live probe above; `test_secrets_store.py` round-trips (env/.env stores + win32 backend typing); CLI prints existence only |
| `/talk` audit + permission gates for both roles, server-side | passed | `test_permissions.py::test_every_owner_command_is_denied_to_friends` (enumerates the FULL owner matrix); every `discord/commands.py` handler calls `PermissionService.authorize_command` then records an audit event via `_begin_interaction` |
| Audit event every owner command attempt by friend (DENIED) | passed | `test_commands_flow.py::test_friend_denied_every_owner_command_and_audited` — parametrized over all 12 owner commands, each asserted `result=denied, reason_code=owner_only_command` in `audit_events` |
| Friend scope: `/talk` creates non-project shared conversation | passed | `test_friend_talk_creates_shared_conversation_without_project` — `project_id=NULL`, `visibility=shared`, topic message persisted |
| `/reply` friend-accessible only | passed | `test_reply_rejected_for_owner_only_conversation` (DENIED + audited `owner_only_conversation`); `test_reply_persists_message_and_updates_status` |
| `/thread` metadata only, no local paths/prompts/memory | passed | `test_thread_metadata_only_for_visible_conversation`, `test_thread_locked_for_owner_only_conversation` |
| `/status` friend-safe (connectivity + availability only) | passed | `test_friend_status_leaks_nothing_sensitive` (\\ / C: quota token path checks); owner variant `test_owner_status_shows_state` |
| Duplicate interaction deliveries are idempotent | passed | `test_duplicate_talk_interaction_is_idempotent` — same interaction id → 1 conversation, "Already processing" response; ledger in `discord_interactions` |
| Friends never touch projects (§25.3) | passed | `project_id=None` enforced in `handle_talk` for ALL roles by construction; `/talk` has no project parameter (§28.3) |
| Role semantics: owner by user ID, friends by role, others stripped | passed | `test_permissions.py` (owner+friend role coexistence, prioritized owner resolution, unknown role → NOT_MEMBER) |
| Offline sync + command registration | in_progress | `discord/bot.py setup_hook` syncs guild-scoped commands (code implemented); network verification requires the owner's Discord application (manual, §28.1) |
| Real-guild smoke test (§28.1 checklist in docs/setup-discord.md) | blocked | requires owner to create Discord application/bot, store token, fill config IDs — manual owner steps, non-blocking for code work |

**Note:** owner-only command BODIES are placeholders by design (each responds
"capability arrives in a later milestone" after passing the same authorization +
audit gate) — no privileged behavior is implemented ahead of its milestone (§36).

## Milestone 3 — CLI adapters and process supervisor — PASSED (fixture suite; live Antigravity blocked on owner)

Evidence on Windows, Python 3.13.14 (2026-11-03):
`.venv\Scripts\python.exe -m pytest --basetemp=pytestm4-final-20261103-1950 -p no:cacheprovider` → **132 passed**;
after the final diagnostic-redaction change,
`.venv\Scripts\python.exe -m pytest --basetemp=.pytest-run-m3-final -p no:cacheprovider tests/unit/test_adapters.py tests/unit/test_supervisor.py`
→ **30 passed**; `ruff check src tests` → all checks passed;
`ruff format --check src tests` → 56 files already formatted.

| Criterion | Status | Evidence |
|---|---|---|
| Adapter protocol and one-shot provider runs | passed | `tests/unit/test_adapters.py` fake CLI contract tests for both adapters |
| Provider status, errors, diagnostics, and usage normalized | passed | `AgentRunResult`; Codex JSONL and Antigravity envelope parser/adapter tests |
| Antigravity soft-denial is blocked at exit code zero | passed | `test_antigravity_status_respects_envelope_denial_and_exit_code`; fake CLI `soft_denial` scenario |
| Malformed output and missing final message fail closed | passed | parser and fake CLI malformed/zero-exit scenarios |
| Cancellation and timeout terminate process trees | passed | `tests/unit/test_supervisor.py`; Windows Job Object stubborn-grandchild case |
| Quota/auth failures pause provider without paid fallback | passed | fake Codex and Antigravity quota/auth scenarios; adapter rejects further runs while paused |
| Live Antigravity CLI contract | blocked | `agy` is not installed/authenticated on this machine; adapter stays disabled per D5 |

## Milestone 4 — Shared Roundtable conversations — PASSED (fixture suite; live provider/guild smoke blocked on owner)

Evidence on Windows, Python 3.13.14 (2026-11-03):
`.venv\Scripts\python.exe -m pytest --basetemp=pytestm4-final-20261103-1950 -p no:cacheprovider` → **132 passed**;
`.venv\Scripts\python.exe -m pytest --basetemp=.testtmp-20261103-1944 -p no:cacheprovider tests/unit/test_migrations.py tests/unit/test_commands_flow.py tests/unit/test_conversation_manager.py` → **39 passed**;
`ruff check src tests` → all checks passed; `ruff format --check src tests` → 63 files already formatted.

| Criterion | Status | Evidence |
|---|---|---|
| Shared Open Room messages and agent replies use one Discord conversation thread | passed | `/talk` creates and stores a thread when Discord permits; manager publisher and command-flow fake-thread test |
| Turn selection is explicit, bounded, and persisted before provider launch | passed | `TurnPolicy`, `conversation_turns`, run/turn rows created before adapter execution; alternation test |
| Structured modes are owner-only; Friends remain Open Room only | passed | `/talk mode` backend gate and owner/friend command-flow tests |
| Debate/Decision preserve independent positions and store a proposed synthesis | passed | independent provider fixture calls, synthesis prompt retaining both positions, proposed decision record |
| Messages link to provider runs and optional session IDs | passed | message `provider_run_id`; turn metadata includes `provider_session_id`; persistence assertions |
| Agents are instructed to distinguish evidence and not invent file/test activity | passed | shared persona policy and mode prompt tests |
| Live provider and Discord-guild smoke | blocked | Antigravity remains disabled pending its sandbox canary; real Discord credentials/guild are owner setup items |

## Milestone 5 — Memory and permanent archive — PASSED (local fixture suite)

Evidence on Windows, Python 3.13.14 (2026-11-03):
`.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=pytestm5-final-20261103-2035 tests` → **135 passed**; after final memory/backup validation refinements, focused migration, memory/backup, and conversation tests → **14 passed**; `ruff check src tests` → all checks passed; `ruff format --check src tests` → 67 files formatted.

| Criterion | Status | Evidence |
|---|---|---|
| Private memory is isolated per agent and project | passed | `test_memory_context_enforces_friend_agent_and_project_boundaries` |
| Friends cannot retrieve memory; accepted shared project items are retrievable | passed | Context access-boundary test; friend-origin/shared-visibility requests return no memory rows |
| Shared proposals require owner approval | passed | candidate shared item is excluded until `MemoryRepository.approve_shared` |
| Two-step owner `/forget` confirms target and invalidates managed backup copies | passed | backup/forget integration test; content-free audit details |
| Backup manifests include schema, size and SHA-256; verification works | passed | `BackupService.create/verify` integration coverage |
| Restore verifies backup, creates a pre-restore backup, and recovers prior DB state | passed | `test_restore_recovers_database_from_verified_backup` |
| Permanent retention; no TTL cleanup | passed | no automatic cleanup path; archives and confirmation records retained absent explicit forget |
| Live provider and Discord guild smoke | blocked | Antigravity sandbox canary and Discord application/guild setup remain owner-dependent |
## Milestone 6 — Event-driven autonomy — PASSED (local fixture suite)

Evidence on Windows, Python 3.13.14 (2026-11-04):
`.venv\Scripts\python.exe -m pytest -p no:cacheprovider --tb=short --basetemp=.m6full-zy4820 tests` → **140 passed**; focused event, migration, command-flow, and conversation suite → **44 passed**; `ruff check src tests` → all checks passed; `ruff format --check src tests` → 72 files formatted.

| Criterion | Status | Evidence |
|---|---|---|
| Discord, Git, test, task, scheduler and agent events normalize into the persisted queue | passed | `EventSources`; Discord `/talk` and `/reply` intake plus dedicated source adapters |
| Duplicate events are suppressed and claims are idempotent | passed | stable canonical fingerprint + configured time bucket; `test_events_dedupe_and_are_processed_once` |
| Event/discussion state recovers after restart | passed | pending/processing records persist; `recover_processing`; bot startup recovery |
| Agent discoveries require structured output, evidence and bounded chain depth | passed | `consume_agent_output`; acknowledgement/malformed output is inert; depth capped at three |
| Auth/quota/dispatch failures do not trigger retry storms | passed | failed dispatches are terminal; `test_dispatch_failure_is_terminal_and_recovery_requeues_interrupted_event` |
| Friends cannot trigger project research or execution | passed | friend commands remain Open Room only; autonomous sources are owner-only and config-gated |
| Live Discord and provider smoke | blocked | Discord application/guild and Antigravity sandbox remain owner-dependent |
## Milestone 7 — Dual-agent implementation and isolated worktrees — PASSED (fixture suite + provider canaries verified)

Evidence on Windows, Python 3.13.14 (2026-11-03/06):
`.venv\Scripts\python.exe -m pytest -p no:cacheprovider --tb=short --basetemp=.m7-manualtmp-91a tests` → **146 passed**;
`ruff check src tests` → all checks passed; `ruff format --check src tests` → 82 files formatted;
`compileall -q src tests` and `agent-roundtable project --help` / `task --help` passed.
Codex workspace-write verified (2026-11-06): `execution.codex_workspace_write_verified: true` set in config.
Antigravity write canary passed (2026-11-06): `agy` created `canary.txt` with `AGY_WRITE_CANARY_PASS` in disposable git repo.

| Criterion | Status | Evidence |
|---|---|---|
| Owner-confirmed project registration and path/credential checks | passed | canonical Git root, controller and symlink/junction rejection, captured branch/HEAD/status, explicit warning resolution/exception flow |
| Separate task worktrees and overlapping-scope locks | passed | `test_worktrees_are_separate_and_write_locks_serialize_overlap`; writes and commits stay outside the registered checkout |
| Owner approval, scoped writes, allowlisted validation and failure handling | passed | `tests/unit/test_execution.py`; runs reject disabled policies/unresolved warnings, out-of-scope paths and failed checks |
| Controller-created local commits and independent reviewer/owner acceptance | passed | commits use task branches with Git hooks disabled; `test_independent_review_and_owner_acceptance_are_required` |
| Push, publish and protected-branch merge require approval | passed | `TaskService.require_remote_approval`; no remote-action execution path is exposed |
| Friend discussion cannot start implementation | passed | task creation/execution are owner CLI capabilities; friend Discord handlers remain discussion-only |
| Codex live workspace-write acceptance | passed | `execution.codex_workspace_write_verified: true` set after owner verification (2026-11-06) |
| Antigravity writer/reviewer acceptance | passed | `agy` 1.5.1 installed + authenticated; write canary `AGY_WRITE_CANARY_PASS` confirmed in disposable git repo (2026-11-06) |
| Real target-project smoke test | pending owner setup | no target path registered yet; owner to run `agent-roundtable project add <path>` |

The test suite exercises both provider roles with fixtures, but the M7 claim that both
installed agents can write concurrently remains blocked until the provider canaries
are completed. Do not enable unattended writes based on fixture results alone.

## Milestone 8 — Windows startup, hardening, and operational release — IMPLEMENTED; owner smoke pending

Evidence on Windows, Python 3.13.14 (2026-11-03):
`.venv\Scripts\python.exe -m pytest -p no:cacheprovider --tb=short --basetemp=.m8-final-suite-773b tests` → **149 passed**;
`ruff check src tests` → all checks passed; `ruff format --check src tests` → 86 files formatted;
`compileall -q src tests` passed; PowerShell AST parsing passed for all three startup scripts.

| Criterion | Status | Evidence |
|---|---|---|
| Current-user Windows logon startup installer/remover | passed | `scripts/install-startup-task.ps1` run 2026-11-06 ? Task Scheduler entry confirmed `State=Ready`, logon trigger, `RunLevel=Limited` |
| Single instance | passed in fixture | Windows named mutex; scheduled task uses `IgnoreNew`; `tests/unit/test_lifecycle.py` |
| Crash recording and restart | passed | task wrapper records exits; persistent controller state records unclean exit; Task Scheduler configured for three retries at five-minute intervals |
| Restart recovery | passed in fixture | `test_startup_recovery_requeues_events_and_marks_active_work_interrupted`; task runs are marked interrupted, worktrees preserved, locks released, and events requeued |
| Sleep/offline reporting | passed | owner/friend status and `docs/operations.md` distinguish local online availability from 24/7 service |
| Disk and retention handling | passed in fixture | configurable free-space warning and new project writes pause below threshold; permanent history is not pruned |
| Security and permission suite | passed | full 149-test suite, including existing role and data-boundary tests |
| Zero-additional-spend and provider limits | passed in documentation/fixtures | no API-key fallback; auth/quota errors pause providers; owner status exposes observed state without remaining-quota estimates |
| Clean install from README | pending owner smoke | real Discord credentials and Windows logon task installation require the owner’s local setup |

The scheduled task has not been installed on this machine during implementation.
Run the documented manual Discord smoke, then install it with
`scripts/install-startup-task.ps1` if automatic logon startup is wanted.
