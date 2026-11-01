# IMPLEMENTATION_STATUS.md

Status values: `not_started`, `in_progress`, `blocked`, `passed`, `failed`.
A criterion is `passed` only when its test command was executed and its output
captured in the documented environment (ARCHITECTURE §35.5). Last updated: 2026-11-01.

## Milestone 0 — Environment and CLI capability verification

| Criterion | Status | Evidence / command |
|---|---|---|
| Windows/Python/Git/CLI versions recorded | passed | `docs/environment-report.md`; `python --version`→3.14.6, `git --version`→2.55.0.windows.3, `codex --version`→0.166.0, `agy`→NOT FOUND, Windows build 10.0.26200 |
| Interactive auth possible for both CLIs, no credentials in repo | blocked | Codex: `codex login status` → "Logged in using ChatGPT" (passed). Antigravity: CLI not installed (owner action required) |
| Each CLI completes a one-shot non-interactive prompt | pending | see M0 runs below |
| Both result formats parsed by a small prototype | pending | see M0 runs below |
| Each provider stops on timeout/cancel | pending | exec/cancel prototype |
| Write capability verified only in disposable worktree | pending | canary in disposable repo |
| Antigravity sandbox/denial behavior verified empirically on this machine | blocked | requires installed `agy` CLI |
| One provider failure doesn't block the other | passed (architectural) | system designed per-provider states; re-verify via adapter contract tests (M3) |

**Blockers:**
- Antigravity CLI (`agy`) not installed. Owner must install the Antigravity CLI and authenticate interactively. Build proceeds with Antigravity `DISABLED`/`SANDBOX_UNVERIFIED` (DECISIONS D5).
- Defender exclusions suggested by `codex doctor` (advisory note, not blocking).

## Milestone 1 — Standalone application foundation — in_progress

| Criterion | Status | Evidence / command |
|---|---|---|
| App starts locally from a documented command | pending | `agent-roundtable status` via venv |
| DB initializes and migrates deterministically | pending | test_migrations_* |
| DB uses WAL + foreign keys | pending | pragma introspection test |
| Restart preserves test records | pending | restart-persistence test |
| Secrets not committed or printed | pending | redaction tests |
| No dependency on `sleeping-dogs-web` | passed | fresh standalone repo; no external imports |

## Milestone 2 — Discord bot and role enforcement — not_started
## Milestone 3 — CLI adapters and process supervisor — not_started
## Milestone 4 — Shared Roundtable conversations — not_started
## Milestone 5 — Memory and permanent archive — not_started
## Milestone 6 — Event-driven autonomy — not_started
## Milestone 7 — Dual-agent implementation and isolated worktrees — not_started
## Milestone 8 — Windows startup, hardening, and operational release — not_started
