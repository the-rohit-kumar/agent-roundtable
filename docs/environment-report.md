# Environment Report — Milestone 0 Verification

Generated: 2026-11-01 · Machine: owner Windows workstation · Purpose: record the
actual software state Agent Roundtable will run on (ARCHITECTURE §20 M0). All
findings below were captured by direct command execution on this machine, not
assumed from documentation.

## Core environment

| Component | Required | Actual | Verdict |
|---|---|---|---|
| Windows | Windows 11, ≥24H2 for Antigravity sandbox | build 10.0.26200 (25H2 family), CoreSingleLanguage, en-GB, 64-bit | MEETS floor; wow-era "August 2026 update" condition for Antigravity sandbox still needs the live canary once `agy` exists |
| Python | 3.12+ supported stable | 3.14.6 | OK (verified discord.py 2.7.1 / pydantic 2.14.2 / pywin32 312 / pytest available for 3.14) |
| Git | Git CLI | 2.55.0.windows.3 (`C:\Program Files\Git\cmd\git.exe`) | OK (supports `worktree add -b`) |
| Disk | sufficient for data root + worktrees | 207.6 GiB free on profile drive | OK (matches 5 GiB warn / 1 GiB fail thresholds we adopt) |
| Python tooling | uv | uv 0.11.29 | OK — lockfile strategy (DECISIONS D11) |

## Provider 1: Codex CLI (OpenAI)

| Item | Finding |
|---|---|
| Installed | codex-cli 0.166.0, standalone package; executable `C:\Users\rohit\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe` |
| Auth | `codex login status` → `Logged in using ChatGPT` (auth.json file backend; ChatGPT tokens present; **no API key stored**) |
| Doctor | `codex doctor`: 22 ok · 4 notes · 3 warn · 0 fail |
| Default sandbox | restricted filesystem + restricted network; approval OnRequest |
| Network | WebSocket 101 to ChatGPT backend; HTTP reachability OK |
| Non-interactive exec | **one-shot run PASSED** (`ROUNDTABLE_HEALTHCHECK_OK`) — fixture: `docs/milestone0/codex_jsonl_fixture_redacted.txt`, surface: `codex_cli_surface.md` |
| Write in disposable worktree | **PASSED** — `workspace-write` canary created `canary.txt` with exact content in a disposable Git repo; no other files touched |
| Stop on cancel/timeout | **PASSED with `--no-daemon`** — run is a supervisable child; harness timeout killed the full tree, zero leftovers. Without `--no-daemon`, exec delegates to the shared app-server daemon (see DECISIONS D12) |
| Flags verified | `--json`, `--sandbox`, `-C`, `-o`, `--ephemeral`, `--skip-git-repo-check`, `--output-schema`, `exec resume`; `--no-daemon` is TOP-LEVEL only |
| Prohibited flags confirmed present | `-s danger-full-access`, `--dangerously-bypass-approvals-and-sandbox`, `--dangerously-bypass-hook-trust`, `--worktree` — all excluded from adapter construction (§33.5) |
| Advisories from doctor | Defender exclusion suggestion for codex.exe helpers (owner advisory); not on a Dev Drive (performance note only); MCP env note (Codex-local, unrelated) |
| Quota | Plan allowance unknown/not exposed — usage ledger will record token counts from `turn.completed` and never estimate remaining quota (§35.4) |

## Provider 2: Antigravity CLI (Google)

| Item | Finding |
|---|---|
| Installed | Antigravity IDE 2.6.0 (winget Google.AntigravityIDE) — %LOCALAPPDATA%\Programs\Antigravity IDE |
| Headless CLI (`agy`) | **NOT FOUND** — not on PATH; not in `%APPDATA%\npm`; no `agy.exe`/`agy.cmd`/`agy.bat` anywhere under the IDE install (only the VS Code-style extension bridge `extensions\agy\dist\extension.js`) |
| Consequence | Adapter ships fail-closed: `DISABLED` → `SANDBOX_UNVERIFIED`; read-only only; **no unattended writes** until positive sandbox canary on this exact build (§27.6, DECISIONS D5) |
| Owner action required | Install/enable the Antigravity CLI (`agy`) per Google's CLI docs, then authenticate interactively with the Google account; after that, M0 re-runs the capability probes and sandbox canary |

## Discord

| Item | Finding |
|---|---|
| Application/bot | Not created yet — owner-manual step before M2 (Developer Portal; `bot` + `applications.commands` scopes; Message Content Intent OFF) |
| Token handling | To be stored in Windows Credential Manager via `agent-roundtable secrets set discord-token` (M2); never in repo/chat |

## Conclusion

- Codex provider: FULLY VERIFIED (auth, one-shot, JSONL parse surface, disposable-write canary, stop-on-cancel).
- Antigravity provider: BLOCKED pending owner CLI install; system proceeds in designed degraded single-agent mode.
- Windows/Python/Git: satisfy all §6.2 stack requirements on Python 3.14.
