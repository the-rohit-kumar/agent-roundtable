# Agent Roundtable — Final Technical Architecture & Implementation Specification

> **For OpenCode:** Read this document in full. Sections 24–39 are a normative implementation addendum and override any less-specific earlier guidance. Do not try to implement all milestones in one pass. The build workflow and copy-ready first prompt are in Section 24.

**Document status:** Final architecture decisions locked; implementation not yet started  
**Version:** 2.0 — implementation-grade specification  
**Target platform:** Windows 11 desktop workstation  
**Deployment model:** Local-first, single-user controller connected to a private Discord server  
**Initial operating-cost target:** ₹0/month in additional API or hosting spend; existing account usage limits apply

---

## 1. Executive summary

Agent Roundtable is a standalone, local-first orchestration system that connects two independently authenticated coding agents through a private Discord server:

1. **Codex Agent — Architect / Reviewer** (Codex CLI authenticated with the user's ChatGPT account).
2. **Antigravity Agent — Engineer / Investigator** (Google Antigravity CLI authenticated with the user's Google account).

The system provides a shared discussion room, independent agent memory, shared project memory, event-driven autonomous discussions, and autonomous code implementation in isolated Git worktrees. It is not a wrapper that merely asks two models the same question and concatenates their answers. It is a durable coordination layer with explicit identities, permissions, event handling, task state, evidence, and execution boundaries.

The controller runs on the user's Windows machine and connects outbound to Discord. It starts automatically at user login. It can only operate while the machine is awake, connected to the internet, and the controller process is running.

The `agent-roundtable` repository is completely separate from `sleeping-dogs-web`. The latter is one possible registered project that the agents may work on through isolated worktrees; it is not part of the Roundtable application itself.

## 2. Locked decisions

These decisions are fixed requirements for implementation.

| Area | Locked decision | Required implementation consequence |
|---|---|---|
| Windows startup | **At user login** | Register a Windows Task Scheduler logon task. Run in the user's account so the existing CLI authentication stores are available. |
| Friend permissions | **Discussion only** | Friends can start and participate in shared discussions. They cannot start research jobs, create implementation tasks, access project files, read private memory, or approve execution. |
| Code-writing policy | **Both agents** | Either agent may be assigned implementation work. Each writer gets its own isolated worktree and branch. They must never concurrently edit the same checkout. |
| Autonomous triggers | **Free** | Any supported event or useful agent-discovered finding may initiate a discussion or task proposal. The controller still applies deduplication, loop prevention, process concurrency, project policy, and provider quota handling. |
| Retention | **Forever** | Conversation records, decisions, memories, task history, evidence references, and audit records have no automatic expiry. They remain until explicitly deleted by the owner. |

### 2.1 Clarifications that prevent conflicting interpretations

- **“Both agents can write”** means both adapters can be selected as a writer. It does not mean two processes can write to the same worktree at the same time. Independent tasks may proceed in parallel in separate worktrees; conflicting work is serialized or split into non-overlapping tasks.
- **“Free triggers”** means there is no narrow trigger allowlist such as “only user messages” or “only Git events.” User requests, local project events, completed tasks, discoveries, agent follow-ups, and configured schedules may all be trigger sources. It does not mean an infinite model-to-model loop, unrestricted shell access, or bypassing usage limits.
- **“Discussion-only friends”** allows friends to initiate a conversation and ask general questions. It does not allow a friend message to grant repository access or trigger a research, implementation, or execution workflow.
- **“Forever retention”** means no time-to-live cleanup. It does not mean secrets are logged. Credentials, access tokens, webhook URLs, and authentication files must never be written to conversation records or operational logs.

## 3. Product scope

### 3.1 In scope

- One private Discord server as the shared user interface.
- Two separately authenticated CLI-based agents.
- Distinct agent personas and visible author attribution.
- Open-ended discussions and structured debate, research, brainstorming, decision, and code-review modes.
- Agent-to-agent message relay and follow-up questions.
- Separate private memory for each agent, plus shared project memory.
- Persistent conversation archive and decision history.
- Event-driven and agent-initiated discussion proposals.
- Project registry with explicitly approved local repository paths.
- Autonomous implementation in isolated Git worktrees.
- Automated allowlisted tests and local commits.
- Owner approval for remote or destructive actions.
- Windows logon startup, process supervision, status reporting, backups, and audit history.

### 3.2 Out of scope for the first release

- Public SaaS hosting or a multi-tenant service.
- A paid OpenAI API or Gemini API integration.
- Autonomous Git push, pull-request publication, or branch merge.
- Direct production deployment, production database mutation, or infrastructure changes.
- Friends' ability to start research or code-execution tasks.
- Unrestricted access to the user's filesystem, browser profile, credentials, or arbitrary local applications.
- A self-bot or automation using a personal Discord user token.
- Guaranteed 24/7 operation while the Windows machine is asleep or powered off.

## 4. User roles and permissions

### 4.1 Owner

The owner has full control over the Roundtable instance:

- Start, stop, and resume discussions and jobs.
- Register or disable projects and configure their policies.
- Start research, implementation, review, and structured decision workflows.
- Approve or reject privileged actions.
- Inspect agent memory, shared memory, execution logs, and audit events.
- Delete selected conversations, memories, tasks, and artifacts.
- Change autonomy, concurrency, and retention-related settings.

### 4.2 Friend

Friends are invited Discord members with discussion-only permissions:

- Start a shared discussion using `/talk`.
- Reply to or continue an existing shared discussion.
- Ask general technical questions and participate in brainstorming.
- Read shared-channel messages and final summaries.

Friends cannot:

- Invoke `/research`, `/implement`, `/review`, `/project`, `/approve`, `/memory`, or `/settings`.
- Trigger a repository scan, file read, shell command, test, or code modification.
- View project paths, private agent memory, owner-only channels, or secrets.
- Approve a task or elevate their own permissions.

All permission checks must be performed in the controller's command handlers and policy service. Hiding a slash command in Discord is not sufficient authorization.

### 4.3 Agent identities

Agents are service identities operated by the local controller, not Discord user accounts. The initial implementation may use one Discord bot identity with clear agent-labelled messages or two dedicated webhook identities for visual distinction. It must not use self-bots or personal user tokens.

### 4.4 Discord interaction model

Use Discord application commands (slash commands) for the first release. This avoids silently ingesting every message in every channel and avoids requiring privileged Message Content access for the initial command-driven conversation model. The Discord application must be installed with the required application-command and bot scopes/permissions. Discord documents application commands as the native interaction model for bots and apps: https://docs.discord.com/developers/docs/interactions/slash-commands

Suggested command matrix:

| Command | Owner | Friend | Purpose |
|---|---:|---:|---|
| `/help` | Yes | Yes | Show permitted commands |
| `/talk` | Yes | Yes | Start a shared open discussion |
| `/reply` | Yes | Yes | Add a message to the current discussion |
| `/thread` | Yes | Yes | Select or inspect a shared discussion thread |
| `/status` | Yes | Limited public status only | Show controller/agent availability without paths or private details |
| `/debate` | Yes | No | Start structured debate |
| `/research` | Yes | No | Start a research job |
| `/implement` | Yes | No | Create an implementation task |
| `/review` | Yes | No | Request independent code review |
| `/projects` | Yes | No | List/register/manage approved projects |
| `/approve` | Yes | No | Approve an owner-gated action |
| `/reject` | Yes | No | Reject an owner-gated action |
| `/stop` | Yes | No | Stop a job or discussion process |
| `/resume` | Yes | No | Resume a paused job or discussion |
| `/memory` | Yes | No | Inspect memory records and proposals |
| `/forget` | Yes | No | Delete selected retained content |
| `/settings` | Yes | No | Inspect or change configuration |

The command names and exact parameters can be finalized during Discord implementation, but the permission matrix is fixed.

## 5. Agent personas and interaction modes

### 5.1 Codex Agent — Architect / Reviewer

Primary strengths to emphasize:

- Architecture and design critique.
- Python/backend implementation and refactoring.
- Reasoning about edge cases and system boundaries.
- Independent implementation review.
- Challenging unsupported claims and identifying missing tests.

It is not restricted to review-only work. It may also be selected as the implementation writer.

### 5.2 Antigravity Agent — Engineer / Investigator

Primary strengths to emphasize:

- Local workspace investigation and engineering experiments.
- Repository analysis and implementation.
- Reproduction of defects and test execution.
- Evidence collection from approved local tools and project files.
- Independent review and challenge of proposed designs.

It is not restricted to implementation-only work. It may also be selected as the reviewer or primary design agent.

### 5.3 Important identity distinction

The Codex CLI process is a separate Codex agent authenticated through the user's ChatGPT account. It is not the same running GPT-6 ChatGPT conversation. Agent Roundtable stores its own prompts, sessions, and memory; it does not automatically inherit this ChatGPT conversation's private context.

### 5.4 Conversation modes

| Mode | Interaction pattern | Expected result |
|---|---|---|
| Open Room | Natural conversation, follow-up questions, disagreement, and idea development | Useful ongoing discussion |
| Debate | Proposal → challenge → rebuttal → evidence check → conclusion or dissent | Decision or explicit unresolved disagreement |
| Research Lab | Separate investigation paths → evidence comparison → gaps → experiment proposal | Research summary with evidence and confidence |
| Brainstorm | Generate and compare ideas without immediately implementing them | Shortlist and next steps |
| Code Review | Inspect patch → identify issues → author response → re-review | Findings by severity and review status |
| Decision | Define options and criteria → compare trade-offs → record decision | Decision record with rationale and dissent |

Open Room is the default. Structured modes are requested explicitly by the owner. Friends can use Open Room only.

### 5.5 Agent communication rules

Both agents must:

- Treat the other agent as an independent reviewer, not as an authority that must be agreed with.
- Challenge assumptions when evidence or reasoning is weak.
- Distinguish observed facts, hypotheses, estimates, and unknowns.
- Supply evidence references where available: source URL, file path, line range, test output, or commit.
- Acknowledge and correct errors explicitly.
- Preserve unresolved disagreements rather than silently overwriting them with the last response.
- Avoid repetitive acknowledgements and empty “continue” turns.
- Never claim to have run a command, inspected a file, or passed a test unless the controller has an execution result supporting that claim.

No hidden chain-of-thought is requested or stored. The archive stores actual messages, concise stated rationale, tool results, evidence references, conclusions, and decisions.

## 6. High-level architecture

```mermaid
flowchart TD
    U[Owner and invited friends] --> D[Private Discord server]
    D --> I[Discord interaction gateway]
    I --> P[Authorization and policy engine]
    P --> O[Roundtable orchestrator]
    E[Local events and agent discoveries] --> ER[Event router]
    ER --> O
    O --> C[Context builder]
    C --> M[(SQLite memory and conversation store)]
    O --> A1[Codex CLI adapter]
    O --> A2[Antigravity CLI adapter]
    A1 --> X[Process supervisor]
    A2 --> X
    X --> W[Workspace and Git worktree manager]
    W --> R[Registered project repositories]
    X --> O
    O --> D
    O --> M
    O --> L[Structured logs and audit events]
```

### 6.1 Architectural style

Use a modular monolith for version 1:

- One Python application process.
- One asyncio event loop.
- One Discord bot connection.
- One local SQLite database.
- Two provider-specific CLI adapters behind a common interface.
- One process supervisor for all child processes.
- No Redis, Kubernetes, cloud database, remote worker, or microservice deployment in the initial release.

This is intentionally small enough to run on a personal Windows workstation and easy to debug. Modules should be separated by responsibility so a future service split remains possible.

### 6.2 Recommended technology stack

| Concern | Technology |
|---|---|
| Language | Python 3.12 or newer supported stable release |
| Async runtime | `asyncio` |
| Discord | `discord.py` 2.x application commands/interactions |
| Persistence | SQLite with foreign keys and WAL mode |
| Data validation | Pydantic models or equivalent strict typed schemas |
| CLI execution | `asyncio.create_subprocess_exec` |
| Git/worktrees | Git CLI invoked with argument arrays, never shell-string concatenation |
| Configuration | YAML/TOML plus environment/Windows credential store for secrets |
| Startup | Windows Task Scheduler logon trigger |
| Testing | `pytest`, `pytest-asyncio`, temporary Git repositories, fake CLI executables |
| Packaging | `pyproject.toml`; lock dependencies for repeatable installs |

SQLite WAL allows readers and a writer to proceed concurrently in common local workloads, but the database must remain on a local filesystem; WAL is not intended for a network filesystem. Keep all database access on the same Windows machine: https://sqlite.org/wal.html

## 7. Component responsibilities

### 7.1 Discord gateway (`discord/`)

Responsibilities:

- Connect to the private Discord server.
- Register and dispatch slash commands.
- Resolve Discord user ID, guild ID, channel ID, role IDs, and interaction context.
- Authorize every command against the role matrix.
- Acknowledge long-running interactions promptly and post updates/results later.
- Route agent outputs to the correct shared or owner-only channel.
- Never send private memory or secrets to friend-visible channels.

Initial channel layout:

```text
Agent Roundtable
├── GENERAL
│   ├── #general-chat
│   ├── #agent-war-room
│   └── #announcements
├── DISCUSSIONS
│   ├── #open-discussion
│   ├── #technical-debates
│   ├── #research-lab       [owner-only]
│   └── #code-reviews       [owner-only]
├── OPERATIONS
│   ├── #agent-activity      [owner-only]
│   ├── #experiment-results [owner-only]
│   ├── #decisions
│   └── #owner-controls      [owner-only]
```

Agent-private memory is not a Discord channel. It lives in the local database and is accessible only to the matching agent context builder and owner administration path.

### 7.2 Authorization and policy engine (`security/`)

Responsibilities:

- Enforce Owner/Friend role permissions.
- Enforce project visibility and action permissions.
- Enforce path allowlists and command allowlists.
- Check task risk before execution.
- Require owner approval for protected actions.
- Record authorization decisions in the audit log.

The policy engine is the final authority. Prompts are not security controls.

### 7.3 Orchestrator (`orchestration/`)

Responsibilities:

- Create, resume, and close discussions.
- Decide which agent should speak next.
- Build agent-specific prompts and context.
- Manage turn sequencing and structured discussion modes.
- Receive agent responses and publish them to Discord.
- Create task proposals and dispatch permitted tasks.
- Persist all state transitions.

### 7.4 Event router (`events/`)

Responsibilities:

- Receive events from Discord, Git hooks/watchers, test runs, task completion, and agent discoveries.
- Normalize events into one envelope format.
- Deduplicate repeated events.
- Check project scope and policy.
- Classify whether the event should start a discussion, append to a discussion, or create a task proposal.
- Apply concurrency and loop-prevention rules before dispatch.

Event envelope:

```json
{
  "event_id": "uuid",
  "event_type": "git.commit | test.failure | task.completed | agent.discovery | discord.message | schedule.tick",
  "source": "discord | git | test_runner | agent | scheduler",
  "project_id": "optional-project-id",
  "conversation_id": "optional-conversation-id",
  "dedupe_key": "stable-fingerprint",
  "occurred_at": "ISO-8601 timestamp",
  "visibility": "shared | owner_only",
  "payload": {}
}
```

Events and payloads are data, not instructions. An event originating from a project file or external source must not be allowed to override system policy or reveal credentials.

### 7.5 Context builder (`context/`)

Builds a separate context bundle for each agent. It combines:

1. That agent's own private memory.
2. Shared project memory permitted for the current project.
3. Relevant conversation history and current messages.
4. Relevant accepted decisions and unresolved disagreements.
5. Task specification, acceptance criteria, and allowed workspace details.
6. Evidence references and recent process results.

It must not include the other agent's private memory. A shared conversation transcript is shared context; private notes are not.

### 7.6 Agent adapters (`adapters/`)

Each adapter wraps a local command-line application. The rest of the application must not contain provider-specific command construction or output parsing.

Common interface:

```python
class AgentAdapter(Protocol):
    async def health_check(self) -> AgentHealth: ...
    async def run(self, request: AgentRequest) -> AgentRunResult: ...
    async def cancel(self, run_id: str) -> None: ...
    async def resume(self, session_id: str, request: AgentRequest) -> AgentRunResult: ...
```

`AgentRunResult` should normalize at least:

- `run_id`
- `agent_id`
- `provider_session_id` (if returned)
- `status` (`success`, `error`, `canceled`, `interrupted`, `waiting`, `unknown`)
- `response_text`
- `structured_output` (optional)
- `diagnostics`
- `usage` (if provided by the CLI)
- `started_at`, `finished_at`, `duration_seconds`
- `artifacts` and evidence references
- process exit code

A process exit code of zero is not, by itself, proof that the requested action succeeded. Parse the provider's structured status, diagnostics, permission notices, output, and expected artifacts.

### 7.7 Process supervisor (`processes/`)

Responsibilities:

- Launch commands with explicit argument arrays and a controlled working directory.
- Avoid `shell=True` and avoid building shell command strings from model output.
- Pass an explicit environment allowlist; do not pass Discord secrets or unrelated environment variables to agent processes.
- Enforce timeouts, output limits, concurrency limits, and cancellation.
- Capture stdout and stderr separately.
- Terminate the full child process tree on cancellation or shutdown.
- Record start/end state, return code, sanitized diagnostics, and run metadata.
- Pause and report authentication, network, permission, or quota errors.

On Windows, use a process-tree termination strategy such as a Job Object or a well-tested equivalent. A `/stop` command must not merely stop waiting for the process while leaving child tools running.

### 7.8 Project registry and workspace manager (`projects/`, `workspaces/`)

A project must be explicitly registered by the owner. Store:

- Project ID and display name.
- Canonical local repository path.
- Git repository status and default branch.
- Allowed path patterns.
- Allowed test/build commands.
- Agent execution policy.
- Project visibility and friend-disclosure policy.
- Worktree root and cleanup policy.

The user's existing `sleeping-dogs-web` project can be registered later with its actual local path. Do not guess its path and do not move Agent Roundtable files into that repository.

## 8. Agent CLI integration requirements

### 8.1 Codex CLI adapter

Expected integration pattern:

- User installs Codex CLI and authenticates interactively with the ChatGPT account.
- The controller uses the cached CLI authentication; it must not read, copy, print, or transmit the Codex authentication store.
- Use `codex exec` for non-interactive runs.
- Use the read-only sandbox for analysis/review tasks.
- Use `--sandbox workspace-write` only when the run is confined to its dedicated worktree and the task is authorized.
- Use structured/JSON output where supported, and normalize it into `AgentRunResult`.
- Persist the Codex session identifier when one is returned so resumption can be supported.

Codex's current documentation describes non-interactive `codex exec`, read-only default execution, `workspace-write` for edits, and session resumption. It also warns against treating broad/full-access execution as the default: https://learn.chatgpt.com/docs/non-interactive-mode and https://learn.chatgpt.com/docs/cli/reference

The user's ChatGPT Go plan includes Codex access, but the actual usage allowance varies by plan and current account limits. The controller must not promise unlimited runs or silently switch to API-key billing: https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan

### 8.2 Antigravity CLI adapter

Expected integration pattern:

- User installs Antigravity CLI and authenticates interactively once.
- The controller uses its cached CLI credentials and does not read or export the credential store.
- Use headless mode (`agy -p ...`) for scripted runs.
- Prefer machine-readable JSON output; use streaming JSON only where progress updates are useful.
- Parse status, response, error, usage, and diagnostics.
- Use the CLI sandbox and narrowly scoped permission allowlists for the required workspace operations.
- Never pass `--dangerously-skip-permissions`.
- Verify that the installed Windows version and CLI build actually enforce the configured sandbox before enabling unattended writes. If enforcement cannot be verified, allow read-only work and pause write tasks.

The current Antigravity CLI documentation describes headless prompt execution, JSON and stream-JSON output, cached credentials, and permission behavior. It specifically notes that headless tools requiring an unavailable approval can be soft-denied while the process may still exit successfully; the adapter therefore must inspect the JSON status and diagnostics rather than relying only on the exit code: https://antigravity.google/docs/cli/headless/

### 8.3 Capability diagnostics

The setup wizard must test each provider independently:

1. Executable found and version recorded.
2. Interactive authentication completed by the user.
3. Non-interactive prompt succeeds.
4. Machine-readable result parses.
5. Read-only task succeeds.
6. Write task succeeds only in a disposable test worktree.
7. A denied command is detected correctly.
8. Timeout and cancellation terminate the process tree.

One unavailable provider must not prevent the other provider from operating in a degraded single-agent mode. There is no automatic paid API fallback.

## 9. Autonomy and trigger semantics

### 9.1 Eligible trigger sources

Any supported, policy-approved source may start or continue activity:

- Owner command in Discord.
- Friend discussion message, for conversational replies only.
- Git commit, branch, or relevant local repository change.
- Test/build failure or completion.
- Completion of another agent task.
- New evidence or a discovery made during an authorized investigation.
- Agent-initiated follow-up based on a meaningful unresolved question.
- Configured scheduled event, if enabled.

“Free” means the system is not limited to one trigger class. The first release should prioritize reliable local event sources and agent-initiated discoveries. External integrations such as GitHub webhooks can be added later without changing the event contract.

### 9.2 Autonomous activity types

The controller distinguishes three levels:

1. **Conversation autonomy:** agents can initiate discussions, ask follow-up questions, challenge an assertion, or reopen a topic when new evidence appears.
2. **Research autonomy:** agents may inspect only registered projects and approved sources when the triggering event and project policy authorize research.
3. **Execution autonomy:** agents may implement eligible tasks in isolated worktrees, run allowlisted tests, and create local commits. Remote publication and protected-branch merges remain owner-gated.

Agent-created task proposals must contain a goal, rationale, affected project, target path scope, risk level, and testable acceptance criteria. The policy engine decides whether the proposal can execute autonomously or must wait for owner approval.

### 9.3 Loop and runaway prevention

The system must support free trigger sources without allowing empty agent-to-agent loops.

Required controls:

- Deduplicate events using stable fingerprints and a persisted event ID.
- Do not wake an agent merely because the other agent sent an acknowledgement.
- Require a new question, new evidence, material disagreement, state change, or explicit continuation reason for another model turn.
- Prevent an agent's own just-produced message from immediately re-triggering the same agent on the same content.
- Limit concurrent discussions and child processes through configurable settings.
- Permit only one active write process per agent per project by default.
- Pause a task when provider authentication, usage quota, or permission state blocks it; never spin-retry continuously.
- Provide owner `/stop` and global shutdown controls.

Recommended initial operational defaults (configurable, not a reduction of eligible trigger types):

| Control | Initial default |
|---|---:|
| Concurrent discussion jobs | 2 |
| Concurrent process per agent | 1 |
| Concurrent write task per project | 1 unless tasks have disjoint path ownership |
| Duplicate-event suppression | 30 minutes for identical event fingerprints |
| Automatic retry after quota/auth failure | 0; wait for explicit recovery/retry |
| Default model turn budget per discussion cycle | 6 agent responses, with continuation requiring new substance |

These defaults are guardrails. They must not prevent a genuinely new event or discovery from starting a new, distinct discussion when the system has capacity.

## 10. Autonomous code execution and Git isolation

### 10.1 Core invariant

**An agent process must never directly edit the project's primary checkout.** All changes happen in controller-created worktrees below a dedicated Roundtable worktree root.

Example layout:

```text
registered-project/
├── .git/
└── ...existing checkout...

agent-roundtable-worktrees/
└── project-id/
    ├── task-uuid-codex/
    └── task-uuid-antigravity/
```

### 10.2 Both-agent implementation policy

- Both agents are eligible to receive implementation tasks.
- For parallel work, each receives a separate branch and worktree.
- The task planner records target path ownership or file-scope declarations where possible.
- If two tasks overlap materially, queue one task or make one agent the writer and the other the reviewer for that task.
- If both produce alternative solutions, preserve both branches and ask for a comparison; do not automatically overwrite one with the other.
- A branch/worktree is not merged into the primary branch automatically.

### 10.3 Execution permission ladder

| Action | Default permission |
|---|---|
| Start discussion or debate | Automatic for owner; discussion-only for friends |
| Read registered project files | Automatic for authorized owner/agent workflows |
| Run approved inspection commands | Automatic |
| Create isolated worktree and branch | Automatic for authorized task |
| Edit files in the worktree | Automatic within approved scope |
| Run allowlisted tests/builds | Automatic |
| Create local commit in task branch | Automatic after validation |
| Push to remote | Owner approval |
| Open/publish pull request | Owner approval |
| Merge into main/master/protected branch | Owner approval |
| Change global Git configuration | Prohibited by default |
| Delete original repository or unrelated files | Prohibited |
| Access secrets, tokens, browser data, or unrelated directories | Prohibited |
| Install dependencies or run arbitrary external commands | Owner approval or explicit project allowlist |
| Deploy or mutate production systems | Prohibited in initial release |

### 10.4 Task lifecycle

```mermaid
stateDiagram-v2
    [*] --> Proposed
    Proposed --> PolicyCheck
    PolicyCheck --> AwaitingApproval: privileged or unclear scope
    PolicyCheck --> Queued: allowed autonomous task
    AwaitingApproval --> Queued: owner approves
    AwaitingApproval --> Rejected: owner rejects
    Queued --> WorktreeReady
    WorktreeReady --> Running
    Running --> Validating
    Running --> Canceled
    Validating --> LocalCommitted: checks pass
    Validating --> NeedsChanges: checks fail
    NeedsChanges --> Running: bounded correction attempt
    LocalCommitted --> Review
    Review --> OwnerReview
    OwnerReview --> Completed: owner accepts
    OwnerReview --> NeedsChanges: changes requested
    Rejected --> [*]
    Completed --> [*]
    Canceled --> [*]
```

### 10.5 Test and commit requirements

Before a local commit:

- Confirm the process modified only the task worktree.
- Capture `git status` and the diff summary.
- Run the project's configured tests/lint/build commands.
- Record command, exit code, duration, and sanitized output.
- Record failed tests honestly; do not label the task successful if required checks failed.
- Create a local commit only when the project policy permits and the patch passes its required checks, or explicitly mark the commit as a draft/failed-check commit if the owner configured that behavior.

## 11. Memory, decisions, and retention

### 11.1 Four memory/archive layers

| Layer | Owner | Visibility | Purpose |
|---|---|---|---|
| Codex private memory | Codex agent | Codex and owner | Its own notes, prior stated conclusions, hypotheses, and open questions |
| Antigravity private memory | Antigravity agent | Antigravity and owner | Its own notes, experiments, implementation context, and open questions |
| Shared project memory | Roundtable | Both agents; owner controls promotion | Accepted project facts, architecture, constraints, evidence, and decisions |
| Conversation archive | Roundtable | According to channel and record visibility | Actual discussion messages, task threads, and summaries |

### 11.2 Memory access rules

- Codex context must never contain Antigravity private memory.
- Antigravity context must never contain Codex private memory.
- Both agents may access shared project memory according to project policy.
- Friends may see only messages explicitly posted in friend-visible channels. They cannot query the memory database.
- Memory is not automatically shared merely because an agent mentions it in a private note.
- A candidate fact becomes shared memory only after the configured acceptance process. Initial default: owner approval for material shared-memory promotion.
- Disagreements are stored as disagreements with both positions and evidence, not collapsed into a single “truth.”

### 11.3 Evidence and provenance

Every memory item should include:

- Unique ID, project ID, memory scope, and agent owner if private.
- Content and category (`fact`, `hypothesis`, `decision`, `constraint`, `lesson`, `open_question`).
- Source message IDs, file path/line references, URLs, commit IDs, or test run IDs.
- Confidence/status (`candidate`, `accepted`, `rejected`, `superseded`).
- Created and updated timestamps.
- Promotion/rejection actor and rationale, where applicable.

### 11.4 Forever retention

- No automatic TTL for messages, memory, decisions, task records, artifacts, or audit events.
- No scheduled deletion of old conversations or memories.
- Backups are retained without a time-based expiry by default and must be encrypted or stored in an appropriately protected local location.
- Compress old structured logs/artifacts rather than silently deleting them.
- Warn the owner when free disk space falls below a configured threshold; pause new large jobs rather than deleting historical data automatically.
- `/forget` is an owner-only explicit deletion workflow. It must identify affected messages, memory, derived summaries, indexes, exports, and backups. Keep a content-free tombstone audit entry so the deletion action itself remains auditable.
- A privacy notice must tell invited friends that messages submitted to Roundtable commands are archived locally indefinitely unless the owner deletes them.

## 12. Data model

SQLite is the initial system of record. Use migrations, foreign keys, indices, and a single controlled write path.

Core tables:

| Table | Purpose | Key fields |
|---|---|---|
| `agents` | Registered agent providers and personas | `agent_id`, `provider`, `display_name`, `enabled` |
| `agent_sessions` | Provider session mapping | `agent_session_id`, `agent_id`, `provider_session_id`, `conversation_id` |
| `projects` | Approved repositories | `project_id`, `name`, `repo_path`, `default_branch`, `policy_json` |
| `conversations` | Discussion and debate sessions | `conversation_id`, `project_id`, `mode`, `visibility`, `status`, `trigger_event_id` |
| `messages` | Actual messages and agent replies | `message_id`, `conversation_id`, `author_type`, `author_id`, `content`, `visibility`, `created_at` |
| `private_memory` | Agent-owned memory | `memory_id`, `agent_id`, `project_id`, `content`, `status`, `source_refs_json` |
| `shared_memory` | Shared project knowledge | `memory_id`, `project_id`, `content`, `status`, `source_refs_json`, `approved_by` |
| `decisions` | Accepted decisions and dissent | `decision_id`, `project_id`, `conversation_id`, `status`, `decision_json` |
| `disagreements` | Unresolved competing positions | `disagreement_id`, `conversation_id`, `positions_json`, `evidence_json`, `status` |
| `events` | Event queue and deduplication | `event_id`, `event_type`, `dedupe_key`, `status`, `payload_json` |
| `tasks` | Structured task proposals | `task_id`, `project_id`, `source`, `goal`, `acceptance_json`, `risk`, `status` |
| `task_runs` | Agent execution runs | `run_id`, `task_id`, `agent_id`, `status`, `process_metadata_json` |
| `worktrees` | Isolated worktree records | `worktree_id`, `task_id`, `agent_id`, `path`, `branch`, `status` |
| `approvals` | Owner-gated actions | `approval_id`, `action_type`, `payload_json`, `status`, `decided_by` |
| `artifacts` | Diffs, logs, reports, test output | `artifact_id`, `task_id`, `path`, `sha256`, `artifact_type` |
| `usage_ledger` | Run counts and provider usage where exposed | `run_id`, `provider`, `usage_json`, `recorded_at` |
| `audit_events` | Security and lifecycle audit | `audit_id`, `actor`, `action`, `target`, `result`, `created_at` |

### 12.1 Database invariants

- Use `PRAGMA foreign_keys=ON`.
- Use WAL mode on local storage.
- Apply schema migrations at startup before the bot accepts commands.
- Enforce unique event IDs and dedupe keys where appropriate.
- Add indices for conversations, project IDs, timestamps, agent IDs, and pending tasks.
- Keep binary/large artifacts in the local data directory and store their hash/path in SQLite.
- Do not store the database on OneDrive, Dropbox, a network share, or a synchronized/network filesystem while WAL mode is enabled.

## 13. Windows runtime and startup

### 13.1 Startup behavior

Register an entry in Windows Task Scheduler with a **logon trigger** for the user's Windows account. Microsoft documents logon triggers as starting a task when the specified user logs on: https://learn.microsoft.com/en-us/windows/win32/taskschd/ilogontrigger-interface

Startup sequence:

1. User logs into Windows.
2. Task Scheduler starts the Roundtable launcher after a short configurable delay.
3. Launcher acquires a single-instance lock.
4. Application loads configuration and secrets.
5. SQLite migrations and integrity checks run.
6. Process supervisor performs local CLI diagnostics.
7. Discord bot connects.
8. Event router resumes persisted pending events/jobs according to recovery policy.
9. Controller reports ready/degraded state in the owner channel.

Run under the user's normal account, not as `SYSTEM`, because the CLI tools' cached authentication is user-scoped. Configure Task Scheduler restart-on-failure and a graceful shutdown path. The machine must be awake and online for Discord connectivity and agent execution.

### 13.2 Local data paths

Recommended default data root:

```text
%LOCALAPPDATA%\AgentRoundtable\
├── config\
├── database\
│   └── roundtable.db
├── memory\
├── artifacts\
├── logs\
├── backups\
└── worktrees\
```

Keep the Git repository itself separate from the runtime data directory. Add `.env`, database files, logs, artifacts, local config, and credentials to `.gitignore`.

### 13.3 Secrets and credentials

- Store the Discord bot token and webhook URLs in Windows Credential Manager or an equivalent OS-backed secret store.
- A local `.env` file may be supported for development only; it must be ignored by Git and must not be copied into task worktrees.
- Let Codex and Antigravity manage their own authentication stores.
- Never pass provider auth files to agents as prompt context.
- Redact tokens, passwords, webhook URLs, and secret-like values from logs and error messages.

## 14. Suggested repository structure

```text
agent-roundtable/
├── src/
│   └── agent_roundtable/
│       ├── main.py
│       ├── config.py
│       ├── lifecycle.py
│       ├── discord/
│       │   ├── bot.py
│       │   ├── commands.py
│       │   ├── permissions.py
│       │   └── publishers.py
│       ├── orchestration/
│       │   ├── roundtable.py
│       │   ├── turn_manager.py
│       │   ├── debate_manager.py
│       │   ├── context_builder.py
│       │   └── task_planner.py
│       ├── adapters/
│       │   ├── base.py
│       │   ├── codex.py
│       │   ├── antigravity.py
│       │   └── result_parser.py
│       ├── events/
│       │   ├── router.py
│       │   ├── models.py
│       │   ├── dedupe.py
│       │   └── sources/
│       │       ├── discord.py
│       │       ├── git.py
│       │       ├── test_runner.py
│       │       └── agent_discovery.py
│       ├── memory/
│       │   ├── repository.py
│       │   ├── private_memory.py
│       │   ├── shared_memory.py
│       │   └── promotion.py
│       ├── projects/
│       │   ├── registry.py
│       │   ├── path_policy.py
│       │   └── models.py
│       ├── execution/
│       │   ├── supervisor.py
│       │   ├── process_tree.py
│       │   ├── worktree_manager.py
│       │   ├── command_policy.py
│       │   └── approvals.py
│       ├── persistence/
│       │   ├── database.py
│       │   ├── migrations/
│       │   └── repositories/
│       └── observability/
│           ├── logging.py
│           ├── audit.py
│           └── health.py
├── prompts/
│   ├── codex_architect.md
│   ├── antigravity_engineer.md
│   └── shared_rules.md
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── fixtures/
├── scripts/
│   ├── install_startup_task.ps1
│   ├── remove_startup_task.ps1
│   ├── doctor.ps1
│   └── backup.ps1
├── docs/
│   ├── architecture.md
│   ├── setup.md
│   ├── security.md
│   └── operations.md
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
└── README.md
```

## 15. Configuration contract

Use a validated configuration model. Secrets should be referenced by credential-store key, not stored in this YAML.

Example configuration:

```yaml
app:
  name: Agent Roundtable
  timezone: Asia/Kolkata
  data_dir: "%LOCALAPPDATA%/AgentRoundtable"
  startup: windows_logon
  single_instance: true

discord:
  guild_id: "SET_DURING_SETUP"
  owner_user_ids: []
  friend_role_ids: []
  channels:
    open_room: "SET_DURING_SETUP"
    decisions: "SET_DURING_SETUP"
    owner_operations: "SET_DURING_SETUP"
  token_credential_key: "agent-roundtable-discord-token"

agents:
  codex:
    enabled: true
    executable: "codex"
    persona_file: "prompts/codex_architect.md"
    default_mode: read_only
  antigravity:
    enabled: true
    executable: "agy"
    persona_file: "prompts/antigravity_engineer.md"
    default_mode: sandboxed

autonomy:
  trigger_policy: all_supported_sources
  agent_initiated_discussions: true
  agent_discovery_events: true
  autonomous_safe_implementation: true
  max_active_discussions: 2
  max_processes_per_agent: 1
  max_write_tasks_per_project: 1
  duplicate_event_window_minutes: 30
  retry_after_auth_or_quota_failure: false

execution:
  worktree_root: "%LOCALAPPDATA%/AgentRoundtable/worktrees"
  auto_run_allowlisted_tests: true
  auto_create_local_commits: true
  owner_approval_required:
    - git_push
    - pull_request_publish
    - protected_branch_merge
    - dependency_install
    - external_network_command
    - destructive_filesystem_action
    - production_deployment
  forbidden:
    - credential_access
    - unrelated_directory_access
    - original_checkout_modification
    - unrestricted_permission_bypass

memory:
  retention: forever
  shared_memory_promotion: owner_approval
  preserve_disagreements: true
  backups_enabled: true
  automatic_ttl_cleanup: false

logging:
  level: INFO
  redact_secrets: true
  retain_audit_events: forever
```

The setup wizard must replace placeholder Discord IDs and verify paths before enabling any feature. `trigger_policy: all_supported_sources` does not override project and execution policy.

## 16. Main workflows

### 16.1 Friend starts a discussion

1. Friend invokes `/talk` in a shared discussion channel.
2. Discord gateway resolves their member role.
3. Authorization service verifies discussion-only access.
4. Conversation is created with `visibility=shared` and no project-file permission.
5. Context builder creates a discussion-only context; no private memory or repository files are loaded.
6. The selected agent responds; the other agent may join if the turn manager finds a useful question or disagreement.
7. Messages are archived indefinitely.
8. Any requested research or implementation is declined as outside friend permissions and can be separately initiated by the owner.

### 16.2 Autonomous agent discovery starts a discussion

1. An authorized local event or running agent produces a discovery.
2. Event router normalizes the event and computes a dedupe key.
3. Policy engine checks project registration, visibility, and event type.
4. Duplicate/loop checks run.
5. Orchestrator creates a shared or owner-only discussion based on the project policy.
6. Context builder supplies only the initiating agent's private memory and permitted shared project context.
7. The second agent is invited to independently challenge or investigate.
8. The result is posted and stored; a separate task proposal is created only if the finding warrants action.

### 16.3 Autonomous implementation

1. An owner command or authorized agent discovery creates a structured task proposal.
2. Task planner validates required fields and acceptance criteria.
3. Policy engine determines whether the task is allowed autonomously or needs owner approval.
4. Worktree manager creates a new branch/worktree per writer.
5. Process supervisor runs the selected CLI with workspace-scoped permissions.
6. The agent implements and returns a structured summary.
7. Controller checks diff scope and executes configured tests.
8. Agent two may review the patch in read-only mode or in a separate review session.
9. Controller stores report, test evidence, diff hash, and local commit.
10. The owner reviews and decides whether to push or merge.

## 17. Reliability and failure handling

| Failure | Required behavior |
|---|---|
| Discord disconnected | Keep local state; reconnect with backoff; do not lose queued events |
| Codex unavailable | Mark Codex degraded; Antigravity may continue independently |
| Antigravity unavailable | Mark Antigravity degraded; Codex may continue independently |
| Both agents unavailable | Keep Discord commands responsive with a clear offline/degraded response; persist eligible events |
| Provider quota exhausted | Pause affected provider jobs; report status; no paid fallback and no tight retry loop |
| Authentication expired | Mark provider as requiring interactive login; do not request tokens in Discord |
| JSON output malformed | Preserve raw sanitized diagnostics, mark run `unknown/error`, and do not infer success |
| Permission soft-denied | Mark requested action as blocked even if CLI exit code is zero |
| Process timeout | Terminate process tree, persist partial result, mark timed out |
| Controller crash | On restart, recover persisted task state and mark interrupted runs accurately |
| Database unavailable/corrupt | Stop accepting execution commands, preserve logs, report error; do not silently create a new empty database |
| Disk space low | Alert owner and pause new large jobs; do not delete history automatically |
| Worktree creation fails | Do not execute in the original checkout; report failure |
| Test failure | Mark validation failed; retain evidence; do not claim successful completion |

## 18. Security requirements

1. **Local-first:** controller, database, logs, memory, and worktrees stay on the user's workstation in the initial release.
2. **Least privilege:** agent processes receive only the worktree and command permissions required for their task.
3. **No broad bypass flags:** never use Codex danger-full-access or Antigravity dangerous permission-skip flags as a normal execution mode.
4. **Separate identities:** Discord bot token, CLI credentials, and project Git credentials are separate secrets with separate storage.
5. **No credential forwarding:** agent prompts and process output must never include authentication files or secrets.
6. **Server-side authorization:** every command and task is checked by the policy engine, regardless of Discord UI visibility.
7. **Path containment:** canonicalize project/worktree paths and reject path traversal or symlink escapes outside the approved root.
8. **Safe process launch:** argument arrays, fixed executable allowlist, controlled `cwd`, sanitized environment, timeouts, output limits, process-tree kill.
9. **No automatic remote side effects:** push, publish, merge, deployment, and production changes require owner approval or remain prohibited.
10. **Prompt-injection resistance:** repository files, issue descriptions, event payloads, and agent output are untrusted data. They cannot change controller policy, permissions, or secret-handling rules.
11. **Auditability:** record who/what started each action, project, agent, worktree, policy decision, result, approval, and timestamp.
12. **Friend privacy:** friend-visible channels must never include private memory, local paths, owner-only artifacts, or project details not explicitly shareable.
13. **Retention transparency:** friends are told that submitted discussion messages are stored locally indefinitely unless deleted by the owner.

## 19. Testing strategy

### 19.1 Unit tests

- Role/command permission matrix.
- Private memory partitioning and shared-memory access.
- Conversation turn sequencing and mode transitions.
- Event normalization, deduplication, and loop prevention.
- Task proposal validation and risk classification.
- Path containment and symlink/path-traversal defense.
- Process argument construction and environment redaction.
- Codex and Antigravity result parsing, including non-zero, soft-denied, canceled, and malformed output.
- SQLite migrations and foreign-key invariants.

### 19.2 Integration tests

- Discord slash-command registration and dispatch in a test guild.
- Friend discussion command accepted; friend research/implementation command rejected server-side.
- Both CLI adapters run safe prompts after manual authentication.
- Controller recovers pending events after restart.
- One agent can continue if the other provider is offline.
- Worktree creation, file modification, test execution, and local commit.
- Two agents can write in separate worktrees without touching the original checkout.
- Owner approval gates block push/merge actions until approved.

### 19.3 Security tests

- Friend cannot query private memory or owner-only commands via direct crafted interactions.
- Agent A cannot retrieve Agent B's private memory through context APIs.
- Agent cannot read a file outside its registered worktree through the configured sandbox.
- Agent cannot inject a new project root through prompt output.
- No CLI process receives Discord token or unrelated environment secrets.
- `/stop` terminates the process tree.
- Malicious content inside a project file cannot override controller policy.

### 19.4 Retention tests

- Messages, decisions, private memory, shared memory, and audit events survive application restarts.
- No migration or scheduled job applies a TTL or deletes old history.
- Backups can be restored and preserve IDs and references.
- `/forget` removes selected content and derived material from the live store and backup set according to the deletion procedure, while preserving a content-free audit tombstone.

## 20. Implementation milestones and acceptance criteria

Build in the order below. Do not begin with autonomous code execution; establish reliable state, identity, and permissions first.

### Milestone 0 — Environment and CLI capability verification

**Deliverables**
- Windows environment diagnostic script.
- Python/Git/Codex/Antigravity version report.
- Separate authentication checks for both CLI providers.
- Disposable test-repository sandbox test.
- Discord application and private test guild created by the owner.

**Acceptance criteria**
- [ ] Windows version/build, Python version, Git version, and CLI versions are recorded.
- [ ] User can authenticate interactively to Codex and Antigravity without placing credentials in the Roundtable repository.
- [ ] Each CLI completes a one-shot non-interactive prompt.
- [ ] Both result formats are parsed by a small prototype.
- [ ] Each provider can be stopped on timeout/cancel.
- [ ] Write capability is verified only in a disposable worktree.
- [ ] Antigravity sandbox and permission-denial behavior is empirically verified on the actual Windows machine before unattended writes are enabled.
- [ ] If one provider fails, its adapter is disabled without blocking the other.

### Milestone 1 — Standalone application foundation

**Deliverables**
- New standalone `agent-roundtable` Git repository.
- Python package structure and locked dependencies.
- Configuration loader and validation.
- SQLite database, migrations, and repositories.
- Structured logging and audit foundation.
- Single-instance process lock and health command.

**Acceptance criteria**
- [ ] Application starts locally from a documented command.
- [ ] Database initializes and migrates deterministically.
- [ ] Database uses WAL and foreign keys.
- [ ] Restart preserves test records.
- [ ] Secrets are not committed or printed.
- [ ] No dependency on `sleeping-dogs-web` exists.

### Milestone 2 — Discord bot and role enforcement

**Deliverables**
- Discord bot connected to the private guild.
- Slash command registration.
- Owner and Friend role mapping.
- Shared and owner-only channel routing.
- Basic `/help`, `/talk`, `/reply`, and `/status` commands.

**Acceptance criteria**
- [ ] Owner can start and continue a discussion.
- [ ] Friend can start and participate in a discussion.
- [ ] Friend attempts to call privileged commands are rejected by backend authorization.
- [ ] Friend status output does not disclose local paths, private memory, tokens, or owner-only jobs.
- [ ] Messages are persisted with correct author, channel, conversation, and visibility metadata.
- [ ] The bot does not require a personal Discord user token.

### Milestone 3 — CLI adapters and process supervisor

**Deliverables**
- Common `AgentAdapter` interface.
- Codex adapter.
- Antigravity adapter.
- Result normalization and parser tests.
- Timeout, cancellation, output limits, and process-tree termination.

**Acceptance criteria**
- [ ] Each provider can execute a one-shot prompt through the adapter.
- [ ] Provider status and error fields are recorded.
- [ ] A soft-denied Antigravity tool action is treated as blocked even if process exit code is zero.
- [ ] Malformed provider output is not treated as a successful run.
- [ ] `/stop` cancels the active process and descendants.
- [ ] Quota/authentication failures pause the provider and do not trigger a paid API fallback.

### Milestone 4 — Shared Roundtable conversations

**Deliverables**
- Conversation and turn manager.
- Open Room mode.
- Codex and Antigravity persona prompts.
- Agent-to-agent relay.
- Structured modes: Debate, Research Lab, Brainstorm, Decision, Code Review.
- Conversation summary and decision records.

**Acceptance criteria**
- [ ] Both agents can contribute to one Discord thread with correct attribution.
- [ ] The controller can select the next agent and preserve turn order.
- [ ] Agents can disagree without the controller forcing consensus.
- [ ] Debate mode can conclude with a decision or unresolved dissent.
- [ ] Each message is archived and linked to its provider run/session when available.
- [ ] The agent does not claim file inspection or test execution unless backed by a run result.

### Milestone 5 — Memory and permanent archive

**Deliverables**
- Per-agent private memory repository.
- Shared project memory repository.
- Context builder with access partitioning.
- Memory proposal/promotion workflow.
- Indefinite retention and owner deletion workflow.
- Local backup/restore scripts.

**Acceptance criteria**
- [ ] Codex receives its own private memory and never receives Antigravity private memory.
- [ ] Antigravity receives its own private memory and never receives Codex private memory.
- [ ] Both agents can use accepted shared project memory.
- [ ] Friends cannot query memory stores.
- [ ] Material shared-memory promotion requires owner approval by default.
- [ ] No TTL cleanup is configured for archived content.
- [ ] Conversations and memories survive restart and backup/restore.
- [ ] `/forget` removes selected content and derived material according to the documented deletion procedure.

### Milestone 6 — Event-driven autonomy

**Deliverables**
- Event envelope and queue.
- Discord, Git, test-run, task-completion, and agent-discovery event sources.
- Deduplication and loop-prevention logic.
- Agent-initiated discussion proposals.
- Concurrency and provider-quota pause handling.

**Acceptance criteria**
- [ ] Each supported event source can initiate or continue a relevant discussion.
- [ ] Agent-discovered evidence can start a new discussion without a human prompt.
- [ ] Repeated identical events are deduplicated.
- [ ] A response containing only acknowledgement does not trigger an endless next turn.
- [ ] New evidence or a material question can continue a discussion.
- [ ] Event and discussion state survive controller restart.
- [ ] Quota/authentication failures do not create retry storms.
- [ ] Friends cannot use this feature to trigger repository research or execution.

### Milestone 7 — Dual-agent implementation and isolated worktrees

**Deliverables**
- Project registry and owner-approved path configuration.
- Worktree manager.
- Task planner and policy engine.
- Both agents configured as eligible writers.
- Allowlisted test runner.
- Local commit and independent review flow.
- Owner approval gates for remote actions.

**Acceptance criteria**
- [ ] A task can be assigned to Codex or Antigravity as writer.
- [ ] Both agents can work on separate tasks in separate worktrees concurrently.
- [ ] A single worktree is never concurrently writable by both agents.
- [ ] The original project checkout remains unchanged during autonomous edits.
- [ ] Only approved project paths and commands are accessible.
- [ ] Required tests run and their output is stored.
- [ ] Failed required tests prevent the task from being marked successful.
- [ ] Local commits are created only in task branches.
- [ ] Push, publish, and protected-branch merge are blocked until owner approval.
- [ ] Friend-originated discussion cannot start implementation.

### Milestone 8 — Windows startup, hardening, and operational release

**Deliverables**
- Task Scheduler installer/remover scripts.
- Startup diagnostics and `/status` health reporting.
- Crash restart and recovery procedure.
- Retention/backup/disk-space warnings.
- Security test suite.
- User setup and operations documentation.

**Acceptance criteria**
- [ ] Controller starts automatically after the configured user's Windows login.
- [ ] Only one controller instance runs at a time.
- [ ] A controller crash is recorded and the process can be restarted by Task Scheduler.
- [ ] Pending tasks are recovered or accurately marked interrupted after restart.
- [ ] Offline/sleep behavior is clearly reported; no claim of 24/7 availability is made.
- [ ] Security and permission tests pass.
- [ ] No API key or paid service is required for the documented zero-additional-spend path.
- [ ] Usage-limit behavior is tested and reported accurately.
- [ ] A clean install can be completed using the README without undocumented manual code changes.

## 21. Release definition

Version 1.0 is releasable only when all of the following are true:

- Both agents can join a shared conversation through the controller.
- The controller enforces Friend discussion-only permissions.
- Private memory is separated by agent and shared memory is explicitly governed.
- All supported trigger types can initiate relevant work without creating infinite loops.
- Both agents can implement in separate isolated worktrees.
- Required tests and execution evidence are preserved.
- Remote/destructive operations remain owner-gated.
- Messages, memories, decisions, and audit history persist indefinitely unless explicitly deleted.
- Windows logon startup and recovery work reliably.
- Security, privacy, retention, and operational documentation is complete.

## 22. Known constraints and implementation caveats

- CLI access is tied to the user's existing account and its current rate limits. Codex is included with ChatGPT Go, but usage limits vary by plan. Antigravity headless use requires prior interactive authentication and remains subject to the account's available quota and CLI permission model.
- The controller does not create model capacity or bypass provider rate limits.
- The Windows machine must remain awake and connected for the local controller to be available in Discord.
- The actual Antigravity sandbox enforcement must be tested on the target Windows build before unattended write permission is enabled.
- SQLite WAL requires local storage and is not a network-filesystem database mode.
- Permanent retention can consume increasing disk space. The application must warn and pause rather than silently delete history.
- A model's statement that an action is safe is not authorization. The controller's policy engine decides what the process may do.
- This specification defines architecture and acceptance criteria; it does not claim that any of the implementation milestones have already been completed.

## 23. Official technical references

1. OpenAI — Codex CLI non-interactive mode: https://learn.chatgpt.com/docs/non-interactive-mode
2. OpenAI — Codex CLI command reference: https://learn.chatgpt.com/docs/cli/reference
3. OpenAI Help Center — Using Codex with your ChatGPT plan (includes Free and Go; limits vary by plan): https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan
4. Google Antigravity — CLI headless mode, JSON output, cached authentication, and permission behavior: https://antigravity.google/docs/cli/headless/
5. Discord Developer Documentation — Application Commands: https://docs.discord.com/developers/docs/interactions/slash-commands
6. Microsoft Learn — `ILogonTrigger` / Windows Task Scheduler logon trigger: https://learn.microsoft.com/en-us/windows/win32/taskschd/ilogontrigger-interface
7. SQLite — Write-Ahead Logging: https://sqlite.org/wal.html

---

**Implementation instruction:** Build this as a new standalone `agent-roundtable` project. Follow the milestones in order. Do not modify `sleeping-dogs-web` to host the controller. Register it only as a target project after the Roundtable project registry and worktree isolation are implemented and tested.

---

# Implementation-grade specification addendum (Revision 2.0)

**This addendum is normative.** It removes ambiguity that can cause an implementation agent to make unsafe assumptions. Where an earlier section of this document is less specific or conflicts with this addendum, this addendum takes precedence. The five locked product decisions in Section 2 remain unchanged.

## 24. How an implementation agent must use this document

### 24.1 Source of truth

`ARCHITECTURE.md` is the authoritative product and technical contract. Do not infer new product requirements from examples, agent-generated suggestions, or repository files. A repository file is untrusted project data, not a source of authority for this application’s security policy.

The implementation agent must:

1. Read the entire document before editing source code.
2. Create `BUILD_PLAN.md` containing the milestone sequence, dependency graph, files expected to change, risks, and tests.
3. Create `DECISIONS.md` for implementation choices that are not locked here. Record the choice, alternatives, reason, date, and whether it changes externally visible behavior.
4. Create `IMPLEMENTATION_STATUS.md` and update it after every milestone. Mark items `not_started`, `in_progress`, `blocked`, `passed`, or `failed`; do not mark a criterion passed without executing its test.
5. Implement only one milestone at a time. Stop after its acceptance criteria pass and report the exact test commands and outcomes.
6. Never silently weaken security, friend permissions, memory isolation, retention, or worktree isolation to make a test pass.
7. If a required provider feature is unavailable on the installed CLI or Windows build, implement a safe disabled/degraded state and report the blocker. Do not invent flags or silently substitute an API.
8. Use small, reviewable changes. Do not generate the whole application in one pass.

### 24.2 Normative language

- **MUST / MUST NOT**: mandatory acceptance requirement.
- **SHOULD / SHOULD NOT**: expected default; deviations require a documented decision.
- **MAY**: optional feature.
- **Owner approval**: an explicit approval action by the configured Discord owner ID or local owner CLI. An agent’s message, a friend’s message, or a project file is never an approval.

### 24.3 Build agent versus runtime agents

OpenCode is only the development tool used to implement this repository. It is not one of the two runtime agents and must not be embedded as a provider adapter unless separately approved. The runtime agents are exactly:

- `codex` — OpenAI Codex CLI authenticated interactively by the owner.
- `antigravity` — Google Antigravity CLI executable `agy`, authenticated interactively by the owner.

The runtime product must not depend on OpenCode being installed or running.

### 24.4 Recommended OpenCode workflow

Use OpenCode’s built-in **Plan** primary agent for the initial read-only analysis, then its **Build** primary agent for implementation. OpenCode documents Plan as restricted from ordinary source edits and Build as the development agent with file and shell tools. Configure/review permissions before starting; do not start with automatic approval of all permission requests. Official references: <https://docs.opencode.ai/docs/agents/> and <https://docs.opencode.ai/docs/permissions/>.

Recommended initial prompt (copy into OpenCode after placing this file at repository root):

```text
Read ARCHITECTURE.md completely. It is the authoritative specification.
Do not start implementation yet.

1. Summarize the architecture and all five locked decisions.
2. Identify contradictions, missing information, platform risks, and provider CLI assumptions.
3. Create BUILD_PLAN.md, DECISIONS.md, and IMPLEMENTATION_STATUS.md only.
4. Break the project into the milestones already defined in ARCHITECTURE.md.
5. For every milestone, list source modules, dependencies, tests, manual setup, and exit criteria.
6. Verify current OpenCode, Python, Git, Codex CLI, Antigravity CLI, Windows, and Discord instructions against their official documentation. Never guess a CLI flag.
7. Do not create or modify the Discord application, access credentials, use paid API keys, or touch any project outside this repository.
8. Stop and report the plan. Wait for my explicit instruction to begin Milestone 0.
```

After the plan is reviewed, start one milestone with an explicit prompt such as:

```text
Implement Milestone 0 only, following ARCHITECTURE.md and BUILD_PLAN.md.
Do not begin Milestone 1. Run every Milestone 0 acceptance test, capture actual
versions and sanitized output, and update IMPLEMENTATION_STATUS.md. Never print,
read into prompts, or commit credentials. If a required sandbox cannot be proven,
leave unattended write execution disabled. Stop and report results.
```

---

## 25. Final version-1 decisions that remove implementation ambiguity

The following choices are defaults for version 1. They do not change the five locked product decisions.

| Topic | Version-1 decision |
|---|---|
| Application architecture | One local Python modular monolith; no cloud services or microservices |
| Discord identity | One Discord bot application; agent identity is shown in embeds/messages with explicit `Codex` and `Antigravity` labels. Do not create agent webhooks in v1. |
| Discord input | Slash commands and interaction payloads only. Do not read ordinary channel messages or enable Message Content Intent in v1. |
| Conversation delivery | One Discord thread per conversation under a configured discussion channel, where permissions allow. If thread creation fails, post to the configured channel with a conversation ID and clear status. |
| Agent response delivery | A bot-authored embed or message with provider name, mode, turn number, timestamp, and response body. Never impersonate a Discord user. |
| Database | SQLite, one local database, migrations, WAL, foreign keys. No network drive or cloud sync location. |
| Runtime OS | Windows 11 desktop under the owner’s normal user account. No elevated administrator requirement for everyday runtime. |
| Time | Store timestamps in UTC; display using configured timezone, default `Asia/Kolkata`. |
| Project registration | Initially through a local interactive CLI, not a Discord-supplied arbitrary path. Owner-only Discord project commands may list/enable/disable existing registrations. |
| Worktree ownership | Controller owns branch/worktree creation, commits, cleanup and metadata. Agent commands must not manage the primary checkout or create remote side effects. |
| Model context | The controller constructs every prompt from persisted messages and memory. Do not rely on a provider session as the authoritative conversation archive. |
| Provider continuity | Store provider session IDs when returned, but treat resume as optional. A fresh run with reconstructed context must always be possible. |
| Research scope | Friend discussions are general conversation only. Owner-authorized research may inspect only registered projects and explicitly permitted external sources. |
| Web/network | No arbitrary shell networking by default. Provider-native research/network features must be capability-probed and separately policy-controlled. |
| Dependency installation | No autonomous global installs. Project dependencies are installed only through owner-approved setup or a project-specific allowlisted command in the sandbox. |
| Deployment | Local workstation only. No AWS, VPS, hosted database, paid API, or public endpoint in v1. |
| UI | Discord is the primary UI; local CLI is used for setup, diagnostics, project registration, backup/restore and maintenance. No web dashboard in v1. |

### 25.1 Meaning of free autonomy

The system must accept every supported, policy-approved trigger source rather than requiring the owner to manually start every conversation. This includes agent-discovered follow-ups. It does not require constant polling, arbitrary internet scraping, or infinite conversations. A trigger is eligible when there is a meaningful new event, question, finding, disagreement, task result, or configured schedule occurrence.

The controller MUST reject or suppress a trigger when it is a duplicate, self-echo, empty acknowledgement, policy violation, or an attempt to exceed concurrency or provider quotas. A suppressed trigger is recorded with a reason. This is a safety and reliability layer, not a change to the `free` trigger decision.

### 25.2 Meaning of both agents may write

Both agents are first-class eligible writers. The task planner may assign a task to either one and may run independent tasks concurrently in separate worktrees. If their write scopes overlap, the controller MUST serialize the tasks or split them into disjoint path scopes. There is no shared writable worktree.

The controller can also ask one agent to produce an alternative implementation in a second worktree. Both results are preserved for comparison; neither overwrites the other automatically.

### 25.3 Friend permissions are strictly discussion-only

A friend may:

- Start a non-project discussion using `/talk`.
- Reply using `/reply` and inspect the discussion identifier using `/thread`.
- Participate in a friend-visible conversation and read friend-visible summaries.

A friend may not:

- Choose a registered project, path, repository, branch, or worktree.
- Invoke research, review, implementation, test, shell, file-read, memory, settings, approval, backup, or maintenance operations.
- Ask the bot to “just inspect” a repository during a friend-originated conversation; the controller must not infer authorization from natural-language wording.
- Query private memory or owner-only channels.

Friend-originated conversations use `project_id = NULL`, `workspace_access = none`, `memory_scope = shared_conversation_only`, and `allowed_tools = none`. They may be answered by the agents using the context included in the conversation, but they cannot cause repository tool execution. The owner can separately start a new owner-authorized research or implementation task after reviewing the conversation.

---

## 26. Runtime lifecycle and service boundaries

### 26.1 Startup state machine

The controller must expose these states:

- `STARTING`: process has started; configuration and database checks are running.
- `DEGRADED`: application is running, but one or more optional dependencies/providers are unavailable.
- `READY`: Discord is connected, database is healthy, policy is loaded, and at least one provider is usable or the app is ready for discussion-only operation.
- `STOPPING`: graceful shutdown has begun.
- `STOPPED`: process has exited cleanly.
- `FATAL`: a required invariant failed, such as unreadable/corrupt database or invalid security configuration.

Provider state is tracked separately per agent: `UNKNOWN`, `READY`, `AUTH_REQUIRED`, `QUOTA_BLOCKED`, `SANDBOX_UNVERIFIED`, `UNAVAILABLE`, `DISABLED`.

A missing provider MUST NOT prevent the other provider or Discord discussion-only functionality from starting. A database or policy failure MUST prevent all agent execution.

### 26.2 Startup sequence

1. Acquire a process-wide single-instance lock.
2. Resolve and validate config paths without printing secrets.
3. Load the Discord token from Windows Credential Manager through a validated Windows credential backend.
4. Open SQLite, enable foreign keys, set WAL, run migrations, and run a quick integrity check.
5. Load Owner/Friend identity and channel configuration.
6. Initialize audit and structured logging.
7. Discover executables and versions for `codex` and `agy`; perform configured read-only health checks only.
8. Load persisted pending events, jobs, approvals, and active-run records.
9. Mark runs that were active during the previous controller shutdown as `interrupted` unless provider status can prove they completed.
10. Connect the Discord bot and sync application commands.
11. Mark the application `READY` or `DEGRADED` and publish a non-sensitive startup report to the owner operations channel.
12. Start the event dispatcher only after policy and database initialization are complete.

### 26.3 Shutdown sequence

On SIGINT, SIGTERM, Windows console close, or Task Scheduler stop:

1. Change state to `STOPPING` and reject new tasks.
2. Stop event intake and persist queue state.
3. Ask running providers to stop; then terminate remaining child process trees after the configured grace period.
4. Persist run status as `canceled` or `interrupted` accurately.
5. Flush database transactions and audit records.
6. Close Discord connection and database.
7. Release the single-instance lock.

A shutdown must never kill the controller before its child process tree is accounted for, except in a hard OS shutdown. On the next start, abandoned runs are reconciled.

### 26.4 Internal module contracts

Use dependency injection: constructors receive interfaces/repositories rather than importing global singletons. Avoid circular imports. The application composition root (`main.py`/`lifecycle.py`) creates implementations and wires them together.

Core interfaces:

```python
from typing import Protocol
from uuid import UUID

class AgentAdapter(Protocol):
    agent_id: str

    async def capabilities(self) -> "AgentCapabilities": ...
    async def health_check(self) -> "AgentHealth": ...
    async def run(self, request: "AgentRequest") -> "AgentRunResult": ...
    async def cancel(self, run_id: UUID) -> None: ...

class ConversationRepository(Protocol):
    async def create(self, conversation: "ConversationCreate") -> "Conversation": ...
    async def get(self, conversation_id: UUID) -> "Conversation | None": ...
    async def append_message(self, message: "MessageCreate") -> "Message": ...
    async def get_context(self, conversation_id: UUID, limit: int) -> list["Message"]: ...

class EventRepository(Protocol):
    async def enqueue_if_new(self, event: "EventCreate") -> tuple["Event", bool]: ...
    async def claim_next(self, worker_id: str) -> "Event | None": ...
    async def complete(self, event_id: UUID, result: dict) -> None: ...
    async def fail(self, event_id: UUID, reason_code: str, retryable: bool) -> None: ...

class PolicyEngine(Protocol):
    async def authorize(self, action: "ActionRequest") -> "PolicyDecision": ...

class WorkspaceManager(Protocol):
    async def create_worktree(self, task: "Task", agent_id: str) -> "Worktree": ...
    async def verify_scope(self, worktree: "Worktree") -> "ScopeCheckResult": ...
    async def finalize(self, worktree: "Worktree", task: "Task") -> "WorktreeResult": ...
```

These are interface examples, not permission to ignore type checking. The implementation must define concrete Pydantic/dataclass models in a shared `domain/` package and unit-test serialization.

---

## 27. Detailed agent adapter and process contracts

### 27.1 Common request model

Each provider run must be created from a validated request. The model must contain at least:

```json
{
  "run_id": "uuid",
  "agent_id": "codex | antigravity",
  "purpose": "discussion | research | review | implementation",
  "conversation_id": "uuid-or-null",
  "task_id": "uuid-or-null",
  "project_id": "uuid-or-null",
  "working_directory": "absolute-canonical-path-or-null",
  "permission_profile": "discussion | read_only | sandboxed_write",
  "prompt": "assembled prompt string",
  "timeout_seconds": 600,
  "max_stdout_bytes": 2000000,
  "max_stderr_bytes": 1000000,
  "provider_session_id": "string-or-null",
  "correlation_id": "uuid"
}
```

Validation invariants:

- `discussion` purpose MUST have no project, task, or working directory and MUST use `permission_profile=discussion`.
- `implementation` MUST have task ID, registered project ID, and a controller-created worktree path.
- `sandboxed_write` MUST be rejected when the provider's effective sandbox status is not verified.
- `working_directory` MUST be contained within the registered project worktree root after resolving junctions/symlinks.
- `timeout_seconds` and output caps MUST be positive bounded configuration values.
- Prompt text MUST be assembled by the context builder, not accepted directly from a friend as a system prompt.

### 27.2 Common result model

Persist one normalized result per run:

```json
{
  "run_id": "uuid",
  "agent_id": "codex",
  "status": "success | error | blocked | canceled | timed_out | interrupted | unknown",
  "response_text": "final user-facing response",
  "provider_session_id": "optional-provider-id",
  "provider_status": "raw-provider-status-or-null",
  "exit_code": 0,
  "started_at": "UTC ISO-8601",
  "finished_at": "UTC ISO-8601",
  "duration_ms": 1234,
  "usage": {},
  "diagnostics": [],
  "artifacts": [],
  "stdout_artifact_id": "uuid-or-null",
  "stderr_artifact_id": "uuid-or-null",
  "parser_version": "adapter-version"
}
```

Do not assume `exit_code == 0` means success. `blocked` is a first-class status, especially for Antigravity soft-denied tool actions. A result with unrecognized status or missing final output is `unknown` or `error`, never `success` by default.

### 27.3 Provider command construction

- Use `asyncio.create_subprocess_exec(executable, *args, cwd=..., env=...)` with an argument list.
- Never use `shell=True`, PowerShell command-string interpolation, `cmd /c` for agent prompts, or shell concatenation of model output.
- The executable must resolve to the owner-installed CLI executable discovered during setup; reject unexpected executable paths unless the owner confirms them.
- Store only sanitized command metadata. Do not store credential values or the full environment.
- Feed prompts through stdin when the CLI supports it. For a CLI that requires a prompt argument, enforce a Windows command-line length cap and context truncation before process launch.
- Create a unique `run_id` and correlation ID before launch.
- Set a controlled working directory to the task worktree or no working directory for discussion-only calls.
- Do not pass `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, Discord token, webhook values, GitHub tokens, or arbitrary `.env` values to the child process. The CLI reads its own existing account-authentication store under the user profile.

### 27.4 Environment allowlist

Construct a new child environment from an explicit allowlist. Candidate Windows variables include `PATH`, `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`, `APPDATA`, `LOCALAPPDATA`, `TEMP`, `TMP`, `SYSTEMROOT`, `WINDIR`, `COMSPEC`, `PATHEXT`, `PROCESSOR_ARCHITECTURE`, and any provider-specific home-directory override that has been documented and verified. `HOME` may be set to the owner profile directory when required by the CLI.

Do not forward all of `os.environ`. Do not forward any variable whose name contains `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL`, or `WEBHOOK` unless an explicit, reviewed provider requirement proves it is a non-secret path/config variable. Provider account login must be completed by the owner interactively before use.

The process supervisor must test that the selected CLI can find its user-level authentication after environment filtering. If not, add only the specific required non-secret environment variable and document why.

### 27.5 Codex adapter requirements

Expected CLI family:

- `codex --version`
- `codex --help`
- `codex exec --help`
- A harmless authenticated prompt using `codex exec`.

The current official documentation describes `codex exec` as non-interactive execution, `--json` as newline-delimited JSON events, read-only execution as the default, `--sandbox workspace-write` for workspace edits, and `codex exec resume` for resumption. The exact flags and event schema MUST be verified against the installed CLI version during Milestone 0. See <https://learn.chatgpt.com/docs/non-interactive-mode> and <https://learn.chatgpt.com/docs/cli/reference>.

Recommended baseline invocation pattern (validate with installed `codex exec --help` before implementation):

```text
codex exec --json --sandbox read-only --cd <working-directory> -
```

For a registered task worktree only, when the Codex sandbox passes the canary checks:

```text
codex exec --json --sandbox workspace-write --cd <task-worktree> -
```

`-` represents prompt input on stdin where supported by the installed CLI. Do not pass `--worktree`: Agent Roundtable owns worktree creation and must not create nested provider-managed worktrees. Do not use `danger-full-access`, `--yolo`, or an equivalent bypass mode. Do not rely on old `--full-auto` behavior.

The adapter must parse JSONL line by line. It must:

- Preserve progress events as run diagnostics where useful.
- Extract the final assistant message from the documented event type(s) verified on the installed version.
- Recognize provider-level failures, sandbox blocks, and missing final messages.
- Save a sanitized raw event artifact for debugging.
- Treat unknown event types as non-fatal diagnostics unless they affect terminal status.
- Fail closed if the adapter cannot identify a final response or detect a blocked execution.

Do not hardcode a speculative event JSON schema from memory. Capture fixtures using the installed CLI during Milestone 0 and test them. Provider session resumption is optional; the Roundtable database and context builder are authoritative.

### 27.6 Antigravity adapter requirements

Expected CLI family:

- `agy --version`
- `agy --help`
- `agy models` if supported by installed version
- `agy -p <prompt> --output-format json` for a harmless authenticated prompt.

The current official headless documentation describes `-p`/`--prompt`, `--output-format text|json|stream-json`, a JSON response envelope with fields such as `status`, `response`, optional `error`, `conversation_id`, and `usage`, and use of cached credentials after interactive login. It also explicitly documents that permission-requiring tools can be soft-denied in headless mode while the process exits with code zero. See <https://antigravity.google/docs/cli/headless/>.

The adapter must:

- Prefer `--output-format json` for final-only runs and `stream-json` only when progress streaming is necessary.
- Parse the provider status, response, error and diagnostics. A non-success provider status is not success, even with exit code zero.
- Detect permission-denial notices from stderr and structured output.
- Mark the requested operation `blocked` when a required tool is soft-denied.
- Store `conversation_id` as an optional provider session ID.
- Not use `--dangerously-skip-permissions`.

Antigravity sandbox policy must be configured and tested independently of the command flag. The current Google documentation describes `enableTerminalSandbox`, a `toolPermission` value such as `proceed-in-sandbox`, CLI permission allow/deny rules, and a Windows requirement of Windows 11 24H2 or later with the August 2026 update or later for its Windows terminal sandbox. It also documents that when the sandbox is unavailable, certain sandbox/auto settings may have no effect or silently fall back to a different mode. See <https://antigravity.google/docs/sandbox?tab=cli> and <https://antigravity.google/docs/cli/modes>.

Therefore:

- The Doctor/setup command MUST capture `winver`/Windows build, `agy --version`, effective sandbox setting and a harmless canary result.
- Do not assume `--mode=auto` means sandbox is active. Detect warnings/fallbacks and fail closed.
- Do not enable unattended writes unless a positive filesystem and command sandbox canary has passed on this exact machine/version.
- If effective sandbox cannot be proven, allow only read-only agent calls and report `SANDBOX_UNVERIFIED`.
- Build a minimal permissions allowlist; do not whitelist generic `command(git)` or arbitrary shells if that would permit `git push` or unrelated commands.

### 27.7 Process supervision on Windows

The process supervisor must support:

- Maximum process count per provider and per project.
- A timeout per run and optional idle timeout.
- Separate stdout/stderr readers that do not deadlock when either pipe fills.
- Maximum captured output bytes, with truncation metadata and a preserved bounded artifact.
- Cancellation with a process-tree kill.
- A Windows Job Object or another verified Windows descendant-process management mechanism. Merely canceling the asyncio task or killing the parent CLI process is insufficient.
- Graceful termination attempt followed by force termination after a short grace interval.
- Recovery state persisted before launch and after completion.
- No hidden process left running after `/stop`, controller shutdown, or task timeout.

Use a tested Windows Job Object implementation, potentially via `pywin32` or a carefully tested `ctypes` wrapper. If the implementation uses a different dependency, document and test descendant termination on the target Windows version.

### 27.8 Prompt and context size control

- Keep a configurable maximum prompt size per provider. Antigravity's prompt is passed as a CLI argument in the documented headless form, so enforce a conservative command-line-safe maximum on Windows.
- Context selection order: current user request/task spec; relevant recent messages; accepted shared decisions; relevant agent-private memory for that agent; source excerpts; older summary.
- Truncate the oldest low-priority context first, not the task acceptance criteria or policy instructions.
- Store a prompt hash, context IDs, prompt-template version and truncation report. Do not persist unrestricted raw system prompts in logs.
- Never include the other agent's private memory or a friend-inaccessible project context.

---

## 28. Discord interaction specification

### 28.1 Discord application setup

The owner manually creates one Discord application and bot in the Discord Developer Portal. The setup documentation must walk through:

1. Create application and bot identity.
2. Enable installation for the owner's private server.
3. Install with the `bot` and `applications.commands` scopes required by the chosen installation flow.
4. Grant only necessary permissions: view configured channels, send messages, embed links, read message history, create/send in the discussion thread type used by the app, and use application commands.
5. Do not enable privileged Message Content Intent in v1.
6. Copy bot token once into the local credential setup command; never paste it into Discord or commit it.
7. Record guild ID, owner user ID, friend role ID, and channel IDs in configuration.

Use official Discord documentation and the currently supported `discord.py` interaction API. Do not hardcode a permission integer from an old blog post; generate/check the exact permission set during setup and document it. References: <https://docs.discord.com/developers/docs/interactions/slash-commands> and <https://discordpy.readthedocs.io/>.

### 28.2 Channel registry

Channel names are suggestions only; numeric Discord IDs are authoritative. Configuration must support:

- `open_room_channel_id`
- `decisions_channel_id`
- `owner_operations_channel_id`
- `research_channel_id`
- `code_reviews_channel_id`
- `activity_channel_id`

Friend-visible channels: `open_room_channel_id` and optionally `decisions_channel_id` for sanitized final decisions. Owner-only channels: operations, research, code reviews and activity. The bot must validate the guild ID and channel visibility before publishing.

If channel IDs are missing, setup mode may create or ask the owner to configure them. Runtime must not guess a channel by a display name alone. If a channel is deleted, report a configuration error rather than posting to a different channel.

### 28.3 Command contract

Every command handler performs authorization on the server side, records an audit event, and acknowledges the Discord interaction before starting slow work. Long-running work is deferred and later updated with a final status. Handle duplicate interaction deliveries idempotently.

**Friend-safe commands**

`/help`
- No privileged details.
- Lists commands available to the invoking user's role.

`/talk topic:<string> mode:<open-room|brainstorm>`
- Creates a non-project shared conversation.
- Topic maximum length is bounded and sanitized.
- `project_id` is never accepted from a friend.
- Starts one agent response and allows the other agent to join when meaningful.

`/reply conversation:<id> message:<string>`
- Appends one message to a friend-visible conversation only.
- Does not create project context or trigger tools.

`/thread conversation:<id>`
- Displays metadata and a Discord thread link if available.
- Must not return local paths, provider prompts, private memory, or owner-only artifacts.

`/status`
- Friends receive only Discord connectivity and whether the discussion service is available. No provider quota details, local paths, job prompts, or owner tasks.

**Owner-only commands**

`/debate topic:<string> project:<optional-registered-project>`
`/research project:<registered-project> question:<string>`
`/implement project:<registered-project> task:<string> writer:<auto|codex|antigravity>`
`/review project:<registered-project> target:<task-id-or-branch>`
`/projects action:<list|status>`
`/approve approval_id:<uuid>`
`/reject approval_id:<uuid> reason:<string>`
`/stop target:<conversation-id-or-task-id>`
`/resume target:<conversation-id-or-task-id>`
`/memory action:<list|propose|promote|reject>`
`/forget target:<id> confirmation:<exact-confirmation-token>`
`/settings action:<show>`

Exact parameter names may be adapted to Discord API constraints, but authorization and semantics must remain unchanged. Project path registration, secret input, backup/restore, and destructive maintenance should be local CLI actions, not Discord free-form paths.

### 28.4 Response publishing

- Include conversation ID, agent label, mode and turn number in every agent response.
- Split long content at safe boundaries under Discord's current message/embed limits. Preserve code fences and avoid splitting a fenced code block where possible.
- If response includes a local artifact, display a short safe artifact name and owner-only retrieval command; never expose an absolute Windows path in a friend-visible channel.
- Post tool/process details only to owner-only channels.
- Sanitize mentions. Agent output must not cause `@everyone`, `@here`, or user role pings. Set allowed mentions to none unless a user explicitly invoked a supported mention command.
- If publish fails after the database message is persisted, queue a publish retry with the same message ID. Do not ask the agent to regenerate its response.

---

## 29. Persistence schema and data integrity requirements

The initial implementation may split the schema into multiple migration files, but the logical model below is required. Use UUID strings for primary keys and UTC ISO-8601 timestamps. Add indexes for foreign keys and high-frequency filters. Use repository classes for queries; do not put raw SQL in Discord command handlers.

### 29.1 Required logical tables

| Table | Required columns (minimum) |
|---|---|
| `schema_migrations` | `version INTEGER PRIMARY KEY`, `applied_at TEXT NOT NULL`, `checksum TEXT NOT NULL` |
| `agents` | `agent_id TEXT PRIMARY KEY`, `provider TEXT UNIQUE NOT NULL`, `display_name TEXT NOT NULL`, `persona_path TEXT NOT NULL`, `enabled INTEGER NOT NULL`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL` |
| `projects` | `project_id TEXT PRIMARY KEY`, `name TEXT UNIQUE NOT NULL`, `repo_path TEXT UNIQUE NOT NULL`, `default_branch TEXT NOT NULL`, `base_visibility TEXT NOT NULL`, `policy_json TEXT NOT NULL`, `enabled INTEGER NOT NULL`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL` |
| `conversations` | `conversation_id TEXT PRIMARY KEY`, `project_id TEXT NULL`, `mode TEXT NOT NULL`, `visibility TEXT NOT NULL`, `origin TEXT NOT NULL`, `status TEXT NOT NULL`, `discord_channel_id TEXT`, `discord_thread_id TEXT`, `created_by_discord_id TEXT`, `trigger_event_id TEXT`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL`, `closed_at TEXT` |
| `messages` | `message_id TEXT PRIMARY KEY`, `conversation_id TEXT NOT NULL`, `author_type TEXT NOT NULL`, `author_id TEXT NOT NULL`, `content TEXT NOT NULL`, `visibility TEXT NOT NULL`, `discord_message_id TEXT UNIQUE`, `provider_run_id TEXT`, `reply_to_message_id TEXT`, `created_at TEXT NOT NULL`, `metadata_json TEXT NOT NULL` |
| `agent_runs` | `run_id TEXT PRIMARY KEY`, `agent_id TEXT NOT NULL`, `conversation_id TEXT`, `task_id TEXT`, `purpose TEXT NOT NULL`, `status TEXT NOT NULL`, `provider_session_id TEXT`, `working_directory TEXT`, `permission_profile TEXT NOT NULL`, `exit_code INTEGER`, `provider_status TEXT`, `started_at TEXT NOT NULL`, `finished_at TEXT`, `duration_ms INTEGER`, `usage_json TEXT NOT NULL`, `diagnostics_json TEXT NOT NULL`, `stdout_artifact_id TEXT`, `stderr_artifact_id TEXT`, `correlation_id TEXT NOT NULL` |
| `events` | `event_id TEXT PRIMARY KEY`, `event_type TEXT NOT NULL`, `source TEXT NOT NULL`, `project_id TEXT`, `conversation_id TEXT`, `dedupe_key TEXT NOT NULL`, `visibility TEXT NOT NULL`, `payload_json TEXT NOT NULL`, `status TEXT NOT NULL`, `attempt_count INTEGER NOT NULL DEFAULT 0`, `created_at TEXT NOT NULL`, `available_at TEXT NOT NULL`, `processed_at TEXT`, `failure_code TEXT`, `UNIQUE(event_type, dedupe_key)` |
| `tasks` | `task_id TEXT PRIMARY KEY`, `project_id TEXT NOT NULL`, `conversation_id TEXT`, `created_by TEXT NOT NULL`, `source TEXT NOT NULL`, `title TEXT NOT NULL`, `goal TEXT NOT NULL`, `acceptance_json TEXT NOT NULL`, `path_scope_json TEXT NOT NULL`, `risk_level TEXT NOT NULL`, `preferred_writer TEXT`, `status TEXT NOT NULL`, `base_commit TEXT`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL`, `completed_at TEXT` |
| `task_runs` | `task_run_id TEXT PRIMARY KEY`, `task_id TEXT NOT NULL`, `agent_id TEXT NOT NULL`, `run_id TEXT NOT NULL`, `role TEXT NOT NULL`, `status TEXT NOT NULL`, `started_at TEXT`, `finished_at TEXT`, `summary TEXT`, `created_at TEXT NOT NULL` |
| `worktrees` | `worktree_id TEXT PRIMARY KEY`, `task_id TEXT NOT NULL`, `agent_id TEXT NOT NULL`, `path TEXT UNIQUE NOT NULL`, `branch TEXT UNIQUE NOT NULL`, `base_commit TEXT NOT NULL`, `status TEXT NOT NULL`, `created_at TEXT NOT NULL`, `removed_at TEXT`, `final_commit TEXT`, `diff_hash TEXT` |
| `write_locks` | `lock_id TEXT PRIMARY KEY`, `project_id TEXT NOT NULL`, `task_id TEXT NOT NULL`, `path_prefix TEXT NOT NULL`, `agent_id TEXT NOT NULL`, `acquired_at TEXT NOT NULL`, `released_at TEXT`, `status TEXT NOT NULL` |
| `approvals` | `approval_id TEXT PRIMARY KEY`, `action_type TEXT NOT NULL`, `target_type TEXT NOT NULL`, `target_id TEXT NOT NULL`, `payload_json TEXT NOT NULL`, `status TEXT NOT NULL`, `requested_by TEXT NOT NULL`, `decided_by TEXT`, `decision_reason TEXT`, `created_at TEXT NOT NULL`, `decided_at TEXT`, `expires_at TEXT` |
| `memory_items` | `memory_id TEXT PRIMARY KEY`, `scope TEXT NOT NULL`, `agent_id TEXT`, `project_id TEXT`, `category TEXT NOT NULL`, `content TEXT NOT NULL`, `status TEXT NOT NULL`, `confidence TEXT NOT NULL`, `source_refs_json TEXT NOT NULL`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL`, `approved_by TEXT`, `approved_at TEXT`, `supersedes_memory_id TEXT` |
| `decisions` | `decision_id TEXT PRIMARY KEY`, `project_id TEXT`, `conversation_id TEXT NOT NULL`, `title TEXT NOT NULL`, `status TEXT NOT NULL`, `decision_text TEXT NOT NULL`, `alternatives_json TEXT NOT NULL`, `evidence_json TEXT NOT NULL`, `dissent_json TEXT NOT NULL`, `created_at TEXT NOT NULL`, `approved_by TEXT`, `approved_at TEXT` |
| `disagreements` | `disagreement_id TEXT PRIMARY KEY`, `conversation_id TEXT NOT NULL`, `topic TEXT NOT NULL`, `positions_json TEXT NOT NULL`, `evidence_json TEXT NOT NULL`, `status TEXT NOT NULL`, `created_at TEXT NOT NULL`, `resolved_at TEXT`, `resolution_decision_id TEXT` |
| `artifacts` | `artifact_id TEXT PRIMARY KEY`, `project_id TEXT`, `task_id TEXT`, `run_id TEXT`, `artifact_type TEXT NOT NULL`, `relative_path TEXT NOT NULL`, `sha256 TEXT NOT NULL`, `size_bytes INTEGER NOT NULL`, `visibility TEXT NOT NULL`, `created_at TEXT NOT NULL`, `metadata_json TEXT NOT NULL` |
| `usage_records` | `usage_id TEXT PRIMARY KEY`, `run_id TEXT NOT NULL`, `provider TEXT NOT NULL`, `usage_json TEXT NOT NULL`, `estimated_cost TEXT`, `recorded_at TEXT NOT NULL` |
| `audit_events` | `audit_id TEXT PRIMARY KEY`, `actor_type TEXT NOT NULL`, `actor_id TEXT NOT NULL`, `action TEXT NOT NULL`, `target_type TEXT`, `target_id TEXT`, `result TEXT NOT NULL`, `reason_code TEXT`, `correlation_id TEXT NOT NULL`, `created_at TEXT NOT NULL`, `details_json TEXT NOT NULL` |
| `discord_interactions` | `interaction_id TEXT PRIMARY KEY`, `discord_user_id TEXT NOT NULL`, `command_name TEXT NOT NULL`, `status TEXT NOT NULL`, `conversation_id TEXT`, `created_at TEXT NOT NULL`, `completed_at TEXT` |
| `backup_runs` | `backup_id TEXT PRIMARY KEY`, `destination TEXT NOT NULL`, `sha256 TEXT NOT NULL`, `status TEXT NOT NULL`, `created_at TEXT NOT NULL`, `completed_at TEXT`, `metadata_json TEXT NOT NULL` |

### 29.2 Required constraints and indexes

- `messages.conversation_id`, `agent_runs.agent_id`, `tasks.project_id`, `task_runs.task_id`, `worktrees.task_id`, `approvals.target_id` (where modeled), and relevant project references must have foreign keys.
- `memory_items.scope` must be `private` or `shared`.
- A private memory item must have `agent_id IS NOT NULL`; a shared memory item must have `agent_id IS NULL`.
- Friend-visible messages must have no project context when the conversation origin is friend.
- `events` must reject duplicate `(event_type, dedupe_key)` pairs.
- `discord_interactions.interaction_id` is a unique idempotency key.
- `write_locks` must prevent overlapping active path scopes in a project. Because prefix overlap is not easily expressed in SQL, enforce it transactionally in a dedicated lock repository and test race conditions.
- `audit_events` are append-only in normal application code. Explicit forget operations may write a redacted tombstone but must not rewrite unrelated audit entries.
- Create indexes on `(conversation_id, created_at)`, `(project_id, created_at)`, `(agent_id, status)`, `(status, available_at)` on events, `(task_id, status)` on runs, and any common owner queue view.
- Set `PRAGMA foreign_keys=ON` on every connection. Set WAL and a bounded `busy_timeout`. Use explicit transaction boundaries for event claims, approval decisions and lock acquisition.

### 29.3 Migration rules

- Every migration has a monotonically increasing integer version and SHA-256 checksum.
- Migrations run before Discord command sync and before event workers start.
- A failed migration puts the app in `FATAL`; do not create a fresh empty DB automatically.
- Back up the database before a destructive migration.
- Never edit an already-applied migration. Add a new migration.
- Include migration tests from an empty database and from the previous released schema.

### 29.4 JSON storage rules

JSON columns are stored as compact UTF-8 JSON text. Validate data using Pydantic/domain models before insert and after read. Do not depend on SQLite JSON extension support unless the installed SQLite version is explicitly checked. Persist schema version for any long-lived JSON payload that may evolve.

---

## 30. Task, conversation, and event state machines

### 30.1 Conversation states

`open`, `waiting_for_agent`, `waiting_for_user`, `paused`, `completed`, `stopped`, `failed`.

Only the orchestrator may transition conversation state. A provider response cannot directly mark a conversation completed without being stored and validated.

### 30.2 Turn selection policy

For each turn, the orchestrator chooses one of:

- `codex`
- `antigravity`
- `both_independent` (parallel only when each receives the same safe context and neither is allowed to write the same target)
- `none` (wait for user or finish)

Default open-room behavior: send the first question to the agent with the most relevant configured role; invite the other agent when it has a distinct challenge, new evidence, or useful contribution. Do not force every agent to reply to every message. Structured Debate and Research Lab can call both agents independently, then compare responses. In Code Review, one agent writes and the other reviews; both may be selected for writer role in separate tasks.

Persist `turn_index`, `agent_id`, `run_id`, `trigger_reason`, `decision`, and `created_at` for each turn. Do not compute the next speaker solely from the last author; use a turn policy with explicit reason.

### 30.3 Task state machine

Required statuses:

- `proposed`
- `policy_check`
- `awaiting_approval`
- `queued`
- `preparing_worktree`
- `running`
- `validating`
- `reviewing`
- `needs_changes`
- `committed_local`
- `completed`
- `rejected`
- `blocked`
- `canceled`
- `failed`
- `interrupted`

All state changes must be validated against an explicit transition map. Store transition actor and reason in `audit_events`. Invalid transitions raise a domain error and do not partially update related records.

### 30.4 Event envelope schema

Required event payload fields:

```json
{
  "event_id": "uuid",
  "event_type": "discord.command | git.commit | git.changed_paths | test.failed | test.completed | task.completed | agent.discovery | agent.follow_up | schedule.tick | controller.recovered",
  "source": "discord | git | test_runner | agent | scheduler | controller",
  "project_id": null,
  "conversation_id": null,
  "dedupe_key": "stable-key",
  "visibility": "shared | owner_only",
  "occurred_at": "UTC ISO-8601",
  "payload_version": 1,
  "payload": {}
}
```

Validate every event type with a separate Pydantic model. Unknown event types are persisted as rejected/quarantined and must not be interpreted as generic instructions. Do not deserialize arbitrary Python objects.

### 30.5 Event dispatch algorithm

1. Validate event envelope and payload schema.
2. Authenticate source where applicable.
3. Check idempotency/dedupe key.
4. Determine visibility and project policy.
5. Apply actor permission policy.
6. Check whether the event is self-generated echo or duplicate.
7. Check concurrency and write lock availability.
8. Create or append to a conversation/task transactionally.
9. Dispatch to a worker.
10. Persist completion/failure and emitted child events.

The router must never call an agent directly from a Discord command handler. Commands create events/jobs; orchestration workers consume them.

### 30.6 Agent discovery output contract

When asked to propose a follow-up/discovery, agents should return structured JSON (validated on the Python side) with:

```json
{
  "kind": "discussion_proposal | research_proposal | task_proposal | no_action",
  "title": "short title",
  "summary": "concise explanation",
  "novelty_reason": "why this is not a duplicate",
  "evidence_refs": [],
  "project_id": null,
  "path_scope": [],
  "acceptance_criteria": [],
  "risk_level": "low | medium | high | critical",
  "requested_writer": "auto | codex | antigravity | null"
}
```

If structured output cannot be parsed or lacks required fields, store the raw response as a diagnostic and do not create an executable task. A discussion proposal may be auto-published under project visibility policy. Research and implementation proposals still pass the policy engine; the proposal itself does not grant permission.

---

## 31. Project manifest and write-scope policy

### 31.1 Owner registration

Use a local command such as:

```powershell
agent-roundtable project add --name "sleeping-dogs-web" --path "C:\path\to\sleeping-dogs-web"
agent-roundtable project list
agent-roundtable project doctor --name "sleeping-dogs-web"
```

These are target command shapes; OpenCode may choose a CLI framework and finalize exact syntax, but the behavior must be available locally. The owner must interactively confirm the canonical path and repository identity. Do not guess the user's path.

Registration checks:

- Path exists and is a directory.
- Path is a Git worktree/repository with a valid Git top-level.
- `git status` is captured.
- Current branch and HEAD SHA are recorded.
- Repository path is canonicalized and not inside the Agent Roundtable repository.
- Project path is not a symlink/junction to an unrelated location.
- Project does not contain a committed credential file or obvious secret file; report findings and require resolution/explicit exception before write tasks.
- Worktree root is outside the registered project's own normal checkout and inside the Roundtable data root.

### 31.2 Project manifest

Each registered project has a validated `project.yaml` or equivalent database-backed policy object. Example:

```yaml
project:
  id: sleeping-dogs-web
  display_name: Sleeping Dogs Web
  repository_path: "C:/Users/OWNER/path/to/sleeping-dogs-web"
  default_branch: main
  enabled: true

visibility:
  default: owner_only
  friend_discussions_may_reference_project: false
  share_final_decisions_with_friends: false

execution:
  autonomous_write_enabled: true
  allowed_agents: [codex, antigravity]
  allowed_write_roots:
    - src/
    - tests/
    - docs/
  denied_paths:
    - .env
    - .env.*
    - secrets/**
    - credentials/**
    - **/*token*
    - **/*secret*
    - **/*.pem
    - **/*.p12
    - **/*.key
    - .git/**
  allowed_commands:
    - ["python", "-m", "pytest"]
    - ["python", "-m", "ruff", "check", "."]
  allow_dependency_install: false
  allow_network: false
  allow_local_commit: true
  require_review_agent: true
```

This is a sample. The owner must supply real path roots, commands and project test instructions. Never assume a project uses `src/`, `pytest`, `main`, or a specific package manager. If tests are unknown, run no arbitrary test command; ask owner during project setup or mark validation unavailable.

### 31.3 Worktree creation algorithm

1. Lock the project repository metadata operation.
2. Verify no existing active worktree path collision.
3. Capture current default branch HEAD SHA as the immutable task base.
4. Generate unique branch `roundtable/<task-short-id>-<agent-id>`.
5. Generate worktree path below `%LOCALAPPDATA%\AgentRoundtable\worktrees\<project-id>\<task-id>-<agent-id>`.
6. Invoke Git using `create_subprocess_exec`, fixed executable, known repository cwd, argument array. Do not shell interpolate paths.
7. Create a worktree/branch from the captured base commit.
8. Validate the actual worktree root and branch using Git output.
9. Apply safe local policy files or provider-specific sandbox configuration if needed.
10. Verify there are no forbidden secrets/untracked credential files in the working tree.
11. Mark worktree `ready` and start the provider.

Use `git worktree add -b <branch> <path> <base_commit>` or an equivalent verified argument sequence. If branch/worktree creation fails, do not run the agent in the original repository. Do not forcibly remove a worktree with uncommitted changes.

### 31.4 Concurrent scope locks

Each task declares one or more path prefixes (e.g. `src/api/`, `tests/api/`). Before granting a write lock, compare canonical relative path scopes case-insensitively on Windows. Treat parent/child prefixes as overlapping. Examples:

- `src/` conflicts with `src/api/`.
- `src/api/` conflicts with `src/api/users.py`.
- `src/api/` does not conflict with `docs/`.

If a task does not declare a path scope, it owns the whole project and no other project write task may run until the lock is released. Lock acquisition and task queue insertion must be atomic.

### 31.5 Diff and commit finalization

After an agent exits:

1. Run `git status --porcelain=v2` in the worktree.
2. List changed/untracked paths.
3. Reject paths outside write scope or denied-path patterns.
4. Hash the diff and store artifact metadata.
5. Run only allowlisted project validation commands.
6. Run independent review where required.
7. If validation passes, controller—not the agent—creates a local commit. Configure author identity per repository only after owner has set it; do not invent a human author. A dedicated `Agent Roundtable` bot author may be used if documented.
8. Store commit SHA and test evidence.
9. Leave branch/worktree available for owner review. Cleanup must be explicit and must not delete uncommitted work.

A task may be marked `completed` after a successful local commit and review result, but this never means merged or deployed. Distinguish `committed_local` from `completed` in Discord status.

---

## 32. Memory, evidence, and retention implementation details

### 32.1 Context boundaries

The context builder accepts a typed `ContextRequest` containing `agent_id`, `conversation_id`, optional `project_id`, purpose, visibility, and authorized source IDs. It must perform access checks for every memory item. Do not implement memory isolation only as a prompt instruction.

For a friend discussion:

- no project memory query;
- no private memory query;
- no file lookup;
- no tool list containing filesystem, shell or project tools;
- only the shared conversation history and generic persona instructions.

For an owner project task:

- load only that agent's private notes;
- load accepted shared memory for the selected project;
- load the relevant shared conversation and decisions;
- load only files/evidence returned by the authorized project scanner;
- never load the other agent's private memory.

### 32.2 Memory creation

Memory is created from actual persisted content, not hidden model reasoning. Each memory proposal must contain `content`, `category`, `source_refs`, `confidence`, `project_id`, `scope`, and `created_by`. The agent may write its own private candidate memory. Shared memory requires owner approval for material items by default. Low-impact shared conversation summaries may be created automatically but must be linked to source messages and clearly marked as summaries, not verified facts.

### 32.3 Evidence source model

`source_refs_json` accepts typed references:

- `message`: message ID.
- `file`: project ID, relative path, optional line range, content hash.
- `commit`: repository ID and commit SHA.
- `test_run`: test run ID and artifact ID.
- `url`: URL, title, retrieval timestamp and content hash where feasible.
- `decision`: decision ID.
- `agent_run`: run ID.

An evidence reference must not store an unverified claim as if it were a test. Keep evidence description and source type distinct.

### 32.4 Permanent retention

No TTL or automatic age-based deletion is allowed. The system of record must retain:

- Discord-originated conversation messages and agent responses;
- task proposals, statuses and transitions;
- provider run metadata, sanitized stdout/stderr artifacts, process outcomes and usage where exposed;
- decisions, disagreements, evidence references and memory proposals;
- audit events and approval records;
- final artifacts, test logs, diff hashes and local commit references.

Logs may be segmented into multiple files for manageability, but old segments must be archived, not automatically deleted. A disk-space threshold must pause artifact-producing jobs and warn the owner. The system must never silently prune history to regain space.

### 32.5 Explicit deletion

If `/forget` or a local delete operation is implemented, it must:

1. Require owner identity plus a second confirmation token generated for that target.
2. Show the target scope and related derived items before deletion.
3. Remove the selected primary records, derived summaries/indexes and referenced artifacts.
4. Rewrite or invalidate backups that contain deleted data; never claim full deletion while a backup still contains it.
5. Preserve only a content-free tombstone containing deletion target ID/hash, actor, timestamp, and operation result.
6. Record the result in audit history without copying deleted content into the audit event.

If complete backup deletion cannot be guaranteed, disable the `/forget` command rather than providing an incomplete deletion guarantee. Owner may manually remove the entire instance/data directory as a final destructive operation with a clear confirmation.

### 32.6 Backup and restore

- Backups are owner-triggered or scheduled by an explicit owner setting.
- The backup process must use SQLite's supported backup API or a clean checkpoint/consistent snapshot; do not copy an active WAL database file naively.
- Each backup has manifest with database schema version, timestamp, file list, size and SHA-256 checksums.
- Store backups under the Roundtable data root by default; warn that local backups on the same disk do not protect against disk loss.
- Apply Windows user-folder ACL protection and recommend BitLocker/device encryption. Do not claim database encryption unless an actual encryption layer is configured and tested.
- Restore is a local CLI action, requires the controller stopped, verifies hashes and migration compatibility, and creates a pre-restore backup.
- Retention forever applies to backups unless the owner explicitly deletes a backup. No automatic backup rotation/deletion by age.

---

## 33. Security and trust boundaries

### 33.1 Threat model

At minimum, design against:

- A friend attempting a privileged command or trying to use natural language to cause file access.
- A compromised Discord account with Friend role.
- Malicious prompt instructions in repository README files, issue text, test output, web pages or artifacts.
- An agent attempting a path traversal or tool command outside its task.
- A provider CLI permission soft-denial that is mistaken for success.
- Duplicate Discord interactions, duplicated local events, controller restart and orphan processes.
- Secrets accidentally present in output, prompt context, project files or environment variables.
- Two agent runs modifying overlapping files.
- An OpenCode build session modifying files outside the Agent Roundtable repository.

The system does not promise protection from a fully compromised Windows owner account, kernel, administrator, or CLI binary. State this limitation in the security documentation.

### 33.2 Path safety

- Normalize Windows paths with `Path.resolve(strict=True)` when possible.
- Compare canonical paths case-insensitively.
- Resolve junctions and symlinks before allowing access.
- Ensure the worktree root is a descendant of the configured Roundtable worktree directory and the target is a valid registered Git worktree.
- Reject `..`, UNC paths, device paths, alternate data streams and path prefixes that escape the approved root unless explicitly required and reviewed.
- Protect denied paths such as `.env*`, credentials, private keys, token files, browser profiles, `.git` internals and unrelated user directories.
- Test path traversal and junction escape attempts on Windows itself.

### 33.3 Prompt injection and untrusted data

Every project file, event payload, web result, test output, agent response and Discord user message is untrusted content. Use clear wrappers such as `UNTRUSTED PROJECT CONTENT` and never concatenate untrusted data into the system policy section. Do not give an agent the secret store, system configuration prompt, or direct approval function.

Only the controller can authorize actions. An agent can propose an action; a policy engine decision is required before execution. An agent must not be able to call the policy engine with a fabricated actor or owner ID.

### 33.4 Audit and redaction

Record actor, action, target, policy outcome, reason code, correlation ID and timestamps. Avoid recording secret values, complete environment dumps, raw authentication output, private key material or webhook URLs. Apply secret-pattern redaction to provider stdout/stderr before Discord publishing and before operational log output. Preserve a secured raw artifact only if it has been scanned/redacted; never persist unredacted secrets merely for debugging.

### 33.5 No dangerous mode flags

The controller and setup scripts MUST NOT use:

- Codex `danger-full-access`, `--yolo`, or `--dangerously-bypass-approvals-and-sandbox` in normal operation.
- Antigravity `--dangerously-skip-permissions`.
- OpenCode `--auto` during initial architecture analysis or by default during development.
- `shell=True` or arbitrary model-generated command strings.

A future temporary diagnostic exception would require owner confirmation, a disposable environment and a documented security review. It is not part of v1.

---

## 34. Configuration, local commands, and setup flow

### 34.1 Configuration layout

Keep non-secret configuration in `%LOCALAPPDATA%\AgentRoundtable\config\config.yaml`. Keep the application's own repository default templates in `config\config.example.yaml`. Secrets live in Windows Credential Manager, not in YAML.

The loader must:

- Expand only documented environment variables in path values.
- Validate config with Pydantic.
- Reject unknown critical keys or invalid enum values.
- Produce a sanitized `config doctor` report.
- Avoid dumping secret values or credential-store contents.
- Support schema version and upgrade migration.

### 34.2 Example runtime configuration

```yaml
schema_version: 1
app:
  name: Agent Roundtable
  timezone: Asia/Kolkata
  data_dir: "%LOCALAPPDATA%/AgentRoundtable"
  single_instance: true
  startup_mode: windows_logon

discord:
  guild_id: null
  owner_user_ids: []
  friend_role_ids: []
  channels:
    open_room: null
    decisions: null
    owner_operations: null
    research: null
    code_reviews: null
    agent_activity: null
  credential_name: "AgentRoundtable.DiscordBotToken"

agents:
  codex:
    enabled: true
    executable: "codex"
    persona_file: "prompts/codex_architect.md"
    default_timeout_seconds: 600
    discussion_profile: read_only
    project_read_profile: read_only
    project_write_profile: sandboxed_write
  antigravity:
    enabled: true
    executable: "agy"
    persona_file: "prompts/antigravity_engineer.md"
    default_timeout_seconds: 600
    discussion_profile: read_only
    project_read_profile: read_only
    project_write_profile: sandboxed_write

autonomy:
  supported_triggers_enabled: true
  agent_initiated_discussions: true
  agent_discovery_events: true
  configured_schedules: []
  max_active_discussions: 2
  max_processes_per_agent: 1
  max_write_tasks_per_project: 1
  duplicate_event_window_minutes: 30
  max_turns_per_discussion_cycle: 6
  retry_auth_failure: false
  retry_quota_failure: false

execution:
  worktree_root: "%LOCALAPPDATA%/AgentRoundtable/worktrees"
  auto_run_project_allowlisted_tests: true
  auto_create_local_commit: true
  auto_push: false
  auto_publish_pull_request: false
  auto_merge: false
  require_owner_approval_for:
    - git_push
    - pull_request_publish
    - protected_branch_merge
    - dependency_install
    - external_network_command
    - destructive_filesystem_action
    - production_deployment
  forbidden:
    - credential_access
    - original_checkout_modification
    - unrestricted_permission_bypass
    - unrelated_directory_access

memory:
  retention: forever
  private_memory_separation: true
  shared_memory_promotion: owner_approval
  preserve_disagreements: true
  automatic_ttl_cleanup: false
  backups_enabled: true
  backup_retention: forever

observability:
  log_level: INFO
  redact_secrets: true
  retain_operational_history: forever
  low_disk_space_threshold_gb: 5
```

This example is not a fully populated production config. Discord IDs and project paths are intentionally null. Setup must block the corresponding feature until the owner configures them.

### 34.3 Required local CLI commands

A local management CLI must provide equivalent behavior to:

```powershell
agent-roundtable --help
agent-roundtable doctor
agent-roundtable status
agent-roundtable setup
agent-roundtable secrets set discord-token
agent-roundtable discord test
agent-roundtable project add --name <name> --path <path>
agent-roundtable project list
agent-roundtable project doctor --name <name>
agent-roundtable backup create
agent-roundtable backup list
agent-roundtable backup verify <backup-id>
agent-roundtable backup restore <backup-id>
agent-roundtable startup install
agent-roundtable startup status
agent-roundtable startup remove
agent-roundtable run-once --agent codex --purpose diagnostic
```

The framework may use Typer, Click, argparse, or another maintained CLI package. The command semantics and safety guarantees are mandatory; exact spelling is an implementation detail.

### 34.4 Initial setup wizard order

1. Detect Windows version and current user profile paths.
2. Verify Python and Git.
3. Detect OpenCode only as an optional development tool; runtime must not require it.
4. Detect Codex CLI and Antigravity CLI and print versions.
5. Guide owner to authenticate to each provider interactively in its own terminal. Never request credentials in a Discord message.
6. Run harmless authenticated health prompts and validate structured output.
7. Verify sandbox capability and canary behavior independently for each provider.
8. Create Discord application/bot manually, collect bot token via hidden local prompt, store in Credential Manager.
9. Configure guild, owner, friend role and channel IDs.
10. Test Discord connection and role checks.
11. Initialize database and migrations.
12. Optionally install Windows logon startup only after foreground `doctor` passes.
13. Register projects only after Milestone 7 exists; do not ask for project paths in the first-run setup wizard.

---

## 35. Implementation quality, testing and observability

### 35.1 Code quality requirements

- Python source uses type hints for public functions and domain models.
- Use `pathlib`, not manual path string concatenation.
- Use timezone-aware UTC datetimes.
- Use structured logging with correlation IDs.
- Use explicit exceptions/domain error codes rather than broad `except Exception: pass`.
- Do not silently coerce invalid provider output into successful output.
- No secrets in exception repr, Pydantic dumps or HTTP/Discord embeds.
- Use async methods consistently for I/O; do not block the Discord event loop with subprocess calls or synchronous SQLite work. Use an async SQLite layer or run database I/O safely off-loop. Keep transactions short.
- Configure `ruff` or an equivalent linter, pytest, and type checking. Pin dependencies and produce a lockfile.
- Document supported Windows/Python versions from actual Milestone 0/1 test results.

### 35.2 Test layers

**Unit tests:** pure domain, permission matrix, state transitions, context partitioning, event dedupe, risk classification, path containment, prompt assembly, result parsers and config validation.

**Integration tests:** temporary SQLite DB, temporary Git repositories, fake provider executable scripts, mock Discord interactions, startup state/recovery, process timeout, descendant termination, backup/restore, worktree concurrency.

**Provider contract tests:** manual opt-in tests against installed Codex and Antigravity CLIs. These must be separated from normal CI/unit tests so normal tests never consume account quota.

**Security tests:** friend-to-owner escalation, project path traversal, symlink/junction escape, secret redaction, prompt injection, soft denial, failed sandbox, child-process escape, blocked push and blocked merge.

### 35.3 Fake CLI fixtures

Build fake executables/scripts for deterministic tests. Required scenarios:

- Valid successful Codex JSONL stream.
- Codex non-zero exit.
- Codex malformed JSON line.
- Codex process exits zero but emits no final assistant message.
- Valid Antigravity JSON success envelope.
- Antigravity JSON error envelope.
- Antigravity status non-success with exit code zero.
- Antigravity success status with a tool soft-denial diagnostic.
- Provider writes large stdout and stderr concurrently.
- Provider ignores graceful termination and leaves child process alive.
- Provider times out.
- Provider executable missing.
- Provider returns malformed Unicode or truncated JSON.

The fake CLIs must not contain actual credentials or call the network.

### 35.4 Required metrics/status fields

Track at least:

- Application state and time since last successful Discord heartbeat.
- Provider state, last health-check timestamp and last successful run.
- Active/pending event count and oldest pending event age.
- Active/pending conversations and jobs.
- Active child process count.
- Worktree count and write-lock count.
- Database size, artifact size, backup size and free disk space.
- Recent provider failures grouped by reason code.
- Quota/authentication blocked state where provider exposes it.

Do not guess exact remaining quota if CLI does not expose it. Show `unknown` rather than fabricated estimates.

### 35.5 Definition of a passed test

A test passes only when:

- The test command ran in the documented environment.
- The exit code and test summary were captured.
- The correct source revision/working tree is known.
- The result is not inferred from code inspection alone.

OpenCode must distinguish `not run`, `blocked`, `failed`, and `passed` in every milestone report.

---

## 36. Milestone-specific build outputs and stop points

The original Milestones 0–8 remain. This section adds explicit outputs and prevents premature cross-phase work.

| Milestone | Required artifacts in the repository | Must not start yet |
|---|---|---|
| 0 — Environment | `docs/environment-report.md`, `scripts/doctor.ps1`, fake CLI fixtures or prototype parser evidence, `DECISIONS.md` updates | Discord bot, database-backed orchestration, project registration, autonomous writes |
| 1 — Foundation | `pyproject.toml`, lockfile, `src/agent_roundtable/`, config models, migrations framework, SQLite repositories, logging, health CLI | Real provider conversations, autonomous event workers, worktrees |
| 2 — Discord | bot setup docs, interaction handlers, permission service, mock interaction tests, real test-guild smoke test | Provider tools from friend commands, project read/write |
| 3 — Adapters | adapter protocols, Codex/Antigravity adapters, parser fixtures, process supervisor, cancel/timeout tests | Free autonomous triggers and write execution |
| 4 — Conversations | Open Room, mode state machines, turn manager, prompts and transcript persistence | Repository reads/writes for friend conversations |
| 5 — Memory | private/shared repositories, context access tests, promotion and backup/restore | Agent memory auto-sharing or TTL deletion |
| 6 — Autonomy | event queue, discovery contract, dedupe, loop prevention, scheduling config | Worktree writes unless Milestone 7 sandbox and policy are ready |
| 7 — Execution | project registry, scope locks, worktrees, allowlisted validation, local commits, owner approval gates | Push/merge/deploy automation |
| 8 — Release | Task Scheduler installer, health/recovery, hardening, clean setup/upgrade docs | Any unapproved cloud or paid-service deployment |

A milestone may expose safe stubs for future interfaces, but it must not implement future-phase privileged behavior early.

---

## 37. Release readiness checklist

Version 1.0 cannot be called complete until each item is tested on the actual Windows workstation, not merely mocked:

- [ ] Clean install in a new virtual environment from the README.
- [ ] Database migrations succeed on an empty database and existing database.
- [ ] Controller starts and stops cleanly from PowerShell.
- [ ] One and only one instance can run.
- [ ] Discord test guild connection and slash commands work.
- [ ] Friend can start a discussion and cannot invoke any owner-only capability.
- [ ] Owner can start structured debate and project-scoped tasks.
- [ ] Both CLIs independently authenticate and run a harmless prompt through their adapters.
- [ ] Codex JSONL fixtures and live output are parsed; no false success on failure.
- [ ] Antigravity JSON fixtures and live output are parsed; soft-denied actions are marked blocked.
- [ ] Effective sandbox for each writing provider is verified; if not, autonomous writes stay disabled for that provider.
- [ ] Agent private memory partition is proven by tests and an actual context inspection that contains no other-agent private memory.
- [ ] Shared memory promotion follows owner approval.
- [ ] A new event starts a relevant discussion; duplicate/echo events are suppressed.
- [ ] Agent follow-up can occur without human prompt when it has a new finding.
- [ ] No endless agent loop occurs in the configured test suite.
- [ ] A task is created in a separate worktree; original checkout remains clean.
- [ ] Two independent tasks can run in separate worktrees; overlapping path scopes serialize.
- [ ] Required test commands are allowlisted and recorded.
- [ ] Local commits exist only on Roundtable task branches.
- [ ] Push and merge attempts are blocked absent owner approval.
- [ ] `/stop` kills the process tree and records the terminal state.
- [ ] Controller restart recovers events and marks interrupted tasks accurately.
- [ ] Permanent archive data survives restart and backup/restore.
- [ ] Disk-space warnings do not trigger automatic deletion.
- [ ] Windows logon startup works under the owner's normal account without elevation.
- [ ] No paid API key or paid hosting is required for the documented local-first path.
- [ ] No automatic API fallback or credit purchase is possible.
- [ ] `README.md`, `docs/setup.md`, `docs/security.md`, `docs/operations.md`, and `docs/troubleshooting.md` are complete.

---

## 38. Current external technical references to verify during implementation

The following official documentation is the reference point as of the date this specification was prepared. CLI behavior changes; Milestone 0 must still inspect the installed executable's `--help` output and validate behavior.

1. OpenCode — Agents: <https://docs.opencode.ai/docs/agents/>
2. OpenCode — Permissions: <https://docs.opencode.ai/docs/permissions/>
3. OpenCode — Configuration: <https://docs.opencode.ai/docs/config/>
4. OpenAI — Codex CLI non-interactive mode: <https://learn.chatgpt.com/docs/non-interactive-mode>
5. OpenAI — Codex CLI command reference: <https://learn.chatgpt.com/docs/cli/reference>
6. OpenAI Help Center — Codex with ChatGPT plans, including Free and Go; usage limits vary: <https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan>
7. Google Antigravity — CLI headless mode, JSON output and permission behavior: <https://antigravity.google/docs/cli/headless/>
8. Google Antigravity — Terminal sandbox: <https://antigravity.google/docs/sandbox?tab=cli>
9. Google Antigravity — CLI execution modes and Windows sandbox requirements: <https://antigravity.google/docs/cli/modes>
10. Discord — Application commands and interactions: <https://docs.discord.com/developers/docs/interactions/slash-commands>
11. `discord.py` documentation: <https://discordpy.readthedocs.io/>
12. Microsoft — Task Scheduler logon trigger: <https://learn.microsoft.com/en-us/windows/win32/taskschd/ilogontrigger-interface>
13. SQLite — Write-Ahead Logging: <https://sqlite.org/wal.html>

### 38.1 Account and cost caveat

Codex is listed by OpenAI as included across ChatGPT plans including Free and Go, but usage limits vary by plan and current account state. The application must use the owner's existing ChatGPT-managed CLI login and must not add an API key automatically. Antigravity has its own account authentication and current quotas/permissions; verify the owner's actual entitlement at setup. No quota is guaranteed. The application must pause on provider limits and never silently begin API billing or paid cloud hosting.

---

## 39. Final implementation instruction

Build Agent Roundtable as a new, standalone Windows-first Python project. This repository is the controller itself. `sleeping-dogs-web` and any other repositories are optional registered target projects, not part of the Roundtable codebase.

Follow the milestones in order. Do not skip environment verification. Do not implement unrestricted agent execution. Do not expose friend-originated conversations to project context. Do not share private memory between agents. Do not let two processes write the same worktree. Do not automatically push, publish, merge or deploy. Do not delete retained records automatically. Do not claim an acceptance criterion passed until it has been tested.

When a requirement cannot be satisfied on the actual installed software, preserve the intended security boundary, mark the capability blocked/degraded, explain the precise issue, and propose a compatible implementation adjustment for owner review.
