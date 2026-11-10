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

After storing the Discord token and verifying configuration, install the optional
current-user logon task with `scripts\install-startup-task.ps1`. See
[`docs/operations.md`](docs/operations.md) for restart behavior, removal, health,
recovery, backups, disk-space handling, and provider limits. The controller runs
only while the user is logged in and the computer is awake.

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

See `IMPLEMENTATION_STATUS.md`. Milestones 1–6 are passed against the fixture suite.
Milestone 7's project policy, isolated worktree, validation, commit, review and owner
approval flows are implemented and covered by the fixture suite. Live dual-provider
acceptance is pending: Codex workspace writes are disabled by default, Antigravity
remains disabled under D5 until its sandbox canary passes, and a real project has not
been registered. Milestone 8 adds the Windows logon-task scripts, restart recovery,
disk safeguards, and operator procedures. Installing the task and running a clean
Discord setup remain owner-side checks; see [`docs/operations.md`](docs/operations.md)
and [`docs/setup-discord.md`](docs/setup-discord.md).
