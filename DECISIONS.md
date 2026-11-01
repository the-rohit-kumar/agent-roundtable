# DECISIONS.md

Implementation choices not locked by ARCHITECTURE.md. Each entry: choice,
alternatives, reason, date, externally-visible-behavior impact.

## D1 — Local CLI framework: argparse (stdlib)

- **Date:** 2026-11-01
- **Alternatives:** Typer, Click.
- **Reason:** Zero extra dependency risk on Python 3.14, full subcommand support
  (`project add`, `secrets set`, `backup create`), and the spec fixes behavior,
  not spelling (§34.3: "exact spelling is an implementation detail").
- **Externally visible behavior:** None.

## D2 — SQLite access: stdlib `sqlite3` serialized off the asyncio loop

- **Date:** 2026-11-01
- **Alternatives:** `aiosqlite` dependency.
- **Reason:** Writes stay in explicit, short transactions; the async facade runs
  each database operation via `asyncio.to_thread` under an `asyncio.Lock`, so the
  event loop is never blocked by DB I/O (§35.1) and transaction boundaries stay
  explicit. Avoids an extra transitive dependency.
- **Externally visible behavior:** None.

## D3 — Windows process-tree termination: ctypes Job Object wrapper (no pywin32)

- **Date:** 2026-11-01
- **Alternatives:** `pywin32` 312 (3.14 wheels available).
- **Reason:** Required surface is small (`CreateJobObjectW`,
  `SetInformationJobObjectW` with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`,
  `AssignProcessToJobObject`, `TerminateJobObject`). `ctypes` keeps the runtime
  dependency set minimal. Spec §27.7 explicitly allows a "carefully tested ctypes
  wrapper" with descendant-kill tests on the target Windows version (tests target
  build 26200).
- **Known micro-race:** a grandchild spawned in the window before job assignment
  could escape; mitigated by assigning the job immediately at spawn and
  graceful-then-force kill ordering. Covered by a stubborn-child fixture test (M3).

## D4 — Single-instance lock: Windows named mutex via ctypes

- **Date:** 2026-11-01
- **Alternatives:** Lock file with PID verification.
- **Reason:** The mutex is atomic at the OS level; PID-file locks are racy across
  stale processes. §26.1/§13.1 require a process-wide single-instance guarantee.
- **Externally visible behavior:** Second instance exits immediately with a clear error.

## D5 — Antigravity adapter initial state: DISABLED / SANDBOX_UNVERIFIED — blocked on `agy`

- **Date:** 2026-11-01
- **Alternatives:** Ship enabled assuming spec-documented flags.
- **Reason:** `agy` was not found on this machine (only Antigravity IDE 2.6.0, and
  no headless CLI on PATH or under the IDE install). Spec §24.1(7) and §27.6
  require failing closed: no invented flags, no unverified writes. §26.1: a
  missing provider must not block the other.
- **Externally visible behavior:** `/status` reports Antigravity unavailable until
  the owner installs/authenticates the CLI.
- **Unblock path:** owner installs the Antigravity CLI + interactive Google login;
  M0 canary re-run; adapter goes read-only first, then `sandboxed_write` only
  after a positive filesystem+command canary on this exact build (26200).

## D6 — Discord identity: one bot application, agent labels in embeds (spec §25)

- **Date:** 2026-11-01
- **Alternatives:** Two agent webhooks; self-bot (prohibited).
- **Reason:** Locked addendum §25: single bot application; no agent webhooks in
  v1; never impersonate a Discord user; no personal user tokens.
- **Externally visible behavior:** Both agents post as the same bot identity with
  explicit `Codex` / `Antigravity` labels in embeds.

## D7 — Config: Pydantic v2 + PyYAML; env-var expansion limited to documented vars (`%LOCALAPPDATA%`)

- **Date:** 2026-11-01
- **Alternatives:** TOML; unrestricted env expansion.
- **Reason:** The spec's example config is YAML (§34.2); strict validation with
  unknown-key rejection is mandated (§34.1); a narrow documented expansion set
  prevents interpolation surprises near secret-like values.
- **Externally visible behavior:** None.

## D8 — Testing: pytest + pytest-asyncio; fake CLI executables; provider contract tests opt-in

- **Date:** 2026-11-01
- **Alternatives:** Live provider tests in normal CI.
- **Reason:** Spec §35.2 separates provider contract tests from normal tests so CI
  never consumes account quota. Fake fixtures cover the §35.3 scenarios.
- **Externally visible behavior:** None.

## D9 — Provider session resumption stored but optional

- **Date:** 2026-11-01
- **Alternatives:** Resume as the primary continuity mechanism.
- **Reason:** Spec §25: the controller constructs every prompt from persisted
  messages and memory; provider session IDs are stored, resume is optional; a
  fresh run with reconstructed context must always be possible.
- **Externally visible behavior:** None.

## D10 — Commit authorship: dedicated bot identity for Roundtable-created local commits

- **Date:** 2026-11-01
- **Alternatives:** Owner identity (forged human author — prohibited); no author.
- **Reason:** §31.5: "do not invent a human author. A dedicated `Agent Roundtable`
  bot author may be used if documented." Author env vars set only for the commit
  invocation, never forwarded elsewhere.
- **Externally visible behavior:** Task-branch commits authored by
  `Agent Roundtable Controller <roundtable@localhost>`; push/PR/merge remain owner-gated.

## D12 — Codex exec runs always use `--no-daemon`

- **Date:** 2026-11-01
- **Alternatives:** Accept the shared app-server daemon hosting exec turns.
- **Reason:** Empirical stop-on-cancel test (M0): without `--no-daemon`, exec runs
  delegate to the shared Codex app-server daemon, so the real model turn does not
  live in the CLI child process we spawn. `codex doctor` itself confirms a daemon
  persists across runs on this machine. A `/stop` or timeout that kills only the
  CLI PID would leave the daemon running the turn — violating the supervisor rule
  that `/stop` must not "merely stop waiting while leaving processes running"
  (§7.7). With the top-level `--no-daemon` flag, the run executes inside the
  spawned child tree and was verified fully killable, leaving no leftovers.
- **Externally visible behavior:** Each provider run spawns its own codex process
  (slightly slower startup than the shared daemon; correctness of cancellation
  wins), and daemon-mode remains untouched for the user's interactive Codex work.

## D11 — uv as the dependency lock tool (uvm.lock → `uv.lock`)

- **Date:** 2026-11-01
- **Alternatives:** pip-tools / pip freeze.
- **Reason:** Spec §14 lists `uv.lock` in the repo layout; `uv` 0.11.29 is installed;
  gives fully resolved hashes for repeatable installs and handles the venv lifecycle.
- **Externally visible behavior:** None.
