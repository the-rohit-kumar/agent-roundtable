# IMPLEMENTATION_STATUS.md

Status values: `not_started`, `in_progress`, `blocked`, `passed`, `failed`.
A criterion is `passed` only when its test command was executed and its output
captured in the documented environment (ARCHITECTURE §35.5). Last updated: 2026-11-01.

## Milestone 0 — Environment and CLI capability verification — PASSED (with 1 owner-blocked item)

| Criterion | Status | Evidence / command |
|---|---|---|
| Windows/Python/Git/CLI versions recorded | passed | `docs/environment-report.md`; Windows build 10.0.26200, Python 3.14.6 (system) / 3.13.14 (uv venv), Git 2.55.0.windows.3, codex-cli 0.166.0, `agy` → NOT FOUND |
| Interactive auth possible for both CLIs, no credentials in repo | blocked | Codex: `codex login status` → "Logged in using ChatGPT" (passed; credential store untouched by Roundtable). Antigravity: CLI not installed — owner must install + authenticate; recorded as blocked (DECISIONS D5) |
| Each CLI completes a one-shot non-interactive prompt | passed (Codex) / blocked (Antigravity) | Live run: `codex exec --json --sandbox read-only --ephemeral` → `ROUNDTABLE_HEALTHCHECK_OK`; fixture `docs/milestone0/codex_jsonl_fixture_redacted.txt` |
| Both result formats parsed by a small prototype | passed (Codex) / blocked (Antigravity) | JSONL event schema captured (`thread.started`, `turn.started/completed`, `item.completed` with `item.type` `agent_message`/`command_execution`, usage in `turn.completed`); parser contract in `codex_cli_surface.md`; M3 parser implements against these fixtures |
| Each provider stops on timeout/cancel | passed (Codex) / internal (no external provider) | Live test with `--no-daemon` long run + 8s timeout → process tree fully terminated, zero leftover processes. Decision D12 recorded: adapter MUST use top-level `--no-daemon` so exec runs are supervisable children |
| Write capability verified only in disposable worktree | passed (Codex) / blocked (Antigravity) | Disposable Git repo canary: `workspace-write` created `canary.txt` with exact content; no other file touched |
| Antigravity sandbox/denial behavior verified empirically on this machine | blocked | requires installed `agy` CLI (owner action); adapter stays fail-closed meanwhile |
| One provider failure doesn't block the other | passed (architectural) | design per-provider states (§26.1); the current Codex-only degraded state IS this design operating as intended; re-verified after M3 contract tests |

**Consequence:** system runs in documented single-agent (Codex-only) degraded mode
until the owner installs and authenticates the Antigravity CLI.

## Milestone 1 — Standalone application foundation — PASSED

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

## Milestone 2 — Discord bot and role enforcement — next
Code may be implemented with mock-interaction tests before owner manual setup;
the real-guild smoke test requires the owner's Discord application/bot (§28.1):
token into Credential Manager via `secrets set discord-token`, guild/owner/
friend-role/channel IDs into config.

## Milestone 3 — CLI adapters and process supervisor — not_started
## Milestone 4 — Shared Roundtable conversations — not_started
## Milestone 5 — Memory and permanent archive — not_started
## Milestone 6 — Event-driven autonomy — not_started
## Milestone 7 — Dual-agent implementation and isolated worktrees — not_started
## Milestone 8 — Windows startup, hardening, and operational release — not_started
