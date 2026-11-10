# Windows operations

## Startup at logon

Install the app first with `uv sync` and run `uv run agent-roundtable migrate`.
Copy and complete the configuration at
`%LOCALAPPDATA%\AgentRoundtable\config\config.yaml`, store the Discord token
with `uv run agent-roundtable secrets set discord-token`, and confirm that
`uv run agent-roundtable doctor` is healthy enough for your selected providers.

From this repository, install the current user's limited-privilege logon task:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-startup-task.ps1
```

The task starts `uv run agent-roundtable run` at logon, ignores a second launch
while an instance is running, and asks Task Scheduler to retry up to three times
at five-minute intervals after a nonzero exit. It stores no password or API key.
The controller's redacted daily logs are under `%LOCALAPPDATA%\AgentRoundtable\logs`;
the wrapper records start and exit codes in `startup-wrapper.log`, and Task
Scheduler history records task-level failures. An abrupt power loss may omit the
wrapper exit entry; the next launch writes a recovery audit record.

After installing the task, set `app.startup_mode: windows_logon` in the config so
`doctor` reports the intended operating mode. Set it back to `manual` after removing
the task.

Remove the task with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\remove-startup-task.ps1
```

Add `-StopController` to stop the scheduled instance before removing the task.
The task starts only after this Windows account logs in. It does not run while
the computer is shut down, guarantee availability during sleep, or create a
remote/cloud worker. Discord reconnects are attempted while Windows is awake.

## Health and recovery

- `uv run agent-roundtable doctor` checks local prerequisites, config, database
  integrity, and free space. It does not send a model request or estimate remaining
  provider quota.
- `uv run agent-roundtable status` reports disk headroom, database health, and
  queued/interrupted tasks. Owner `/status` also reports provider CLI/sandbox state,
  quota/auth pauses observed during actual runs, uptime, and task counts. Friend
  `/status` remains limited to Discord connectivity and discussion availability.
- Low disk space is a warning at the configured threshold. New project write tasks
  pause below it; history and backups are never silently deleted.
- On restart, processing events are requeued, in-flight tasks and provider runs are
  marked `interrupted`, worktrees are preserved, and stale write locks are released.
  Review `agent-roundtable task list`; do not assume interrupted edits were
  validated or committed. Create a new owner-approved task after inspecting retained
  worktrees.
- The controller does not automatically retry authentication or quota failures.
  `/status` reports `AUTH_EXPIRED` or `QUOTA_BLOCKED` after such a provider failure.
  Recover authentication interactively or wait for the provider's allowance to
  recover. Allowances vary by account and plan; the controller neither estimates
  remaining quota nor falls back to paid API usage.

## Backups and disk

Backups are local SQLite snapshots and include integrity metadata. Use:

```powershell
uv run agent-roundtable backup create
uv run agent-roundtable backup list
$backupId = "paste-a-backup-id-from-the-list"
uv run agent-roundtable backup verify $backupId
```

Backups use the same local data drive by default, so copy a verified backup to
separate owner-managed storage for disaster recovery. Keep the live SQLite database
on a local disk, not a synced or network folder. Retention is permanent unless the
owner explicitly uses the forgetting workflow. Free space is reported; no old
history is pruned automatically.

## Provider limits and cost

The zero-additional-spend path uses the installed Codex CLI with the user's existing
ChatGPT plan and does not require an API key. Provider plan limits and availability
vary; successful CLI execution does not imply unlimited usage. Authentication and
quota errors pause that provider for the current controller process and do not
trigger paid fallback or automatic retry storms. Antigravity remains disabled until
its local sandbox canary has been completed and D5 is updated with evidence.
