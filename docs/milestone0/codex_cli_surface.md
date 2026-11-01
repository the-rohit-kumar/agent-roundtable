# Codex CLI — versioned fixtures captured 2026-11-01

Purpose: record the ACTUAL command surface and output behavior of the installed
Codex CLI so adapters never rely on guessed flags (ARCHITECTURE §24.1(6), §27.5).

| Item | Value |
|---|---|
| Version | `codex-cli 0.166.0` |
| Executable | `C:\Users\rohit\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe` |
| Auth | ChatGPT plan login (`codex login status` → `Logged in using ChatGPT`), auth.json file backend, no API key stored |
| Doctor | 22 ok · 4 notes · 3 warn · 0 fail (`codex doctor`, 2026-11-01) |
| Default sandbox | restricted filesystem + restricted network, approval OnRequest |
| Connectivity | WebSocket handshake to ChatGPT backend OK (HTTP 101), HTTP reachability OK |

## Verified non-interactive command surface (`codex exec --help`, full transcript in codex_exec_help.txt)

- `codex exec [OPTIONS] [PROMPT]` — prompt as argument, **or `-` / omitted → prompt read from stdin** (stdin appended as `<stdin>` block if both given).
- `--json` — print events to stdout as JSONL.
- `-s, --sandbox <read-only|workspace-write|danger-full-access>` — `danger-full-access` PROHIBITED by policy (§33.5). `workspace-write` only for verified task worktrees.
- `-C, --cd <DIR>` — working root. Used to point at the task worktree.
- `-o, --output-last-message <FILE>` — writes final agent message to a file (reliable final-message extraction).
- `--skip-git-repo-check` — allow running outside a Git repo (needed for discussion-only runs with no cwd).
- `--ephemeral` — run without persisting session files (used for discussion-only context where archiving is Roundtable-owned).
- `--output-schema <FILE>` — JSON Schema for the final response (used for §30.6 discovery contract).
- `-m, --model`, `-c key=value`, `--enable/--disable <FEATURE>`, `-a/--ask-for-approval <on-request|never>`.
- `codex exec resume <SESSION_ID>` / `codex exec fork` — provider session continuity (stored, optional; DECISIONS D9).

## Bypass flags — prohibited for normal operation (§33.5, mapped to this build)

- `-s danger-full-access`
- `--dangerously-bypass-approvals-and-sandbox`
- `--dangerously-bypass-hook-trust`
- `--worktree` (Roundtable owns worktree creation; never create nested provider-managed worktrees — §27.5)

## Planned baseline invocation (validated against the flags above)

- Discussion / read-only run:
  `codex exec --json --sandbox read-only --ephemeral --skip-git-repo-check -C <dir-or-temp> -`  (prompt on stdin)
- Task run in a Roundtable-owned worktree (after sandbox canary):
  `codex exec --json --sandbox workspace-write -C <task-worktree> -o <artifact-last-message.txt> -`
- Structured discovery proposal:
  same + `--output-schema <prompts schema file>` where supported.

No token/user encoding needed: the CLI reads its own auth store. Roundtable must
NOT pass `OPENAI_API_KEY` or any secret env var (§27.3/§27.4).

## Fixture policy

JSONL event schema is NOT hardcoded from memory. The first live one-shot run
captures a redacted fixture under `docs/milestone0/codex_jsonl_fixture_redacted.txt`,
and the real parser (M3) is written against the captured event types, failing
closed on unknown/malformed lines (§27.5).

## Live verifications on this machine (2026-11-01)

| Test | Result |
|---|---|
| One-shot non-interactive prompt (`read-only`) | PASSED — `ROUNDTABLE_HEALTHCHECK_OK`; JSONL fixture saved |
| Write canary in disposable Git repo (`workspace-write`) | PASSED — `canary.txt` created with exact content; only expected untracked file |
| Stop-on-cancel (`read-only` long run, harness timeout 8s with `--no-daemon`) | PASSED — process tree fully terminated, no leftover processes |
| M``sys (event types observed across both fixtures) | `thread.started`, `turn.started`, `item.started`/`item.completed` with `item.type` in {`agent_message`,`command_execution`}, `turn.completed` (usage) |

Adapter rule derived from stop test: runs MUST use `codex --no-daemon exec ...`
(top-level `--no-daemon` flag, before `exec`) so the work executes in a
supervisable child process tree instead of the shared app-server daemon.
