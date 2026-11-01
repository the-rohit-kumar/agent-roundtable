# Agent Roundtable

Local-first Windows orchestrator connecting **Codex CLI** (OpenAI, ChatGPT auth) and
**Antigravity CLI** (Google) through a private Discord server. `ARCHITECTURE.md` is the
authoritative specification; `BUILD_PLAN.md`/`DECISIONS.md`/`IMPLEMENTATION_STATUS.md`
track the build. Locked decisions: Windows logon startup, friends are discussion-only,
both agents may implement in isolated worktrees, free event-driven triggers with
safety guardrails, retention forever.

## Requirements

- Windows 10/11 (developed and verified on Windows 11 build 26200)
- Python **3.12+** (verified on 3.14.6)
- Git CLI (verified 2.55)
- [uv](https://docs.astral.sh/uv/) (verified 0.11.29)
- Codex CLI installed + `codex login` (ChatGPT plan), verified: 0.166.0
- Antigravity CLI (`agy`) installed + authenticated — currently the only blocking
  optional provider; the system runs in documented single-agent (Codex-only) mode
  until it is available (see `DECISIONS.md` D5)

## Install (repeatable)

```powershell
uv sync --extra dev      # creates .venv, installs runtime + dev deps from uv.lock
uv run agent-roundtable migrate   # creates %LOCALAPPDATA%\AgentRoundtable, applies schema, seeds identities
uv run agent-roundtable doctor    # environment + config diagnostics
uv run agent-roundtable status    # controller/database status overview
```

`pytest` (run via `uv run pytest`), `ruff` checks, and type annotations are part of
the definition of done for every milestone (§35.1).

## Configuration

Non-secret configuration lives at `%LOCALAPPDATA%\AgentRoundtable\config\config.yaml`
(copy `config/config.example.yaml` as the starting point). The Discord bot token is
NEVER stored in config or the repository — Milestone 2 adds
`agent-roundtable secrets set discord-token` which writes it to Windows Credential
Manager (referenced by name only).

Never place the database, logs, artifacts, or backups on OneDrive/Dropbox or a
network share (SQLite WAL requirement; §12.1).

## Development layout

```
src/agent_roundtable/   controller package
prompts/                agent persona prompts (Milestone 4)
tests/                  unit, integration, security, fixtures
scripts/                PowerShell helpers (doctor, startup tasks, backup)
docs/                   architecture, setup, security, operations
```

## Milestone status

See `IMPLEMENTATION_STATUS.md`. Milestone 1 (application foundation) is the current
build target; Milestone 2 (Discord bot) comes next and requires the manual Discord
application setup documented in `docs/setup.md` (when Milestone 8 release docs land)
and in `ARCHITECTURE.md` §28.1.
