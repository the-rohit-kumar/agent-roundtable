"""Local management CLI (§34.3). argparse-based (DECISIONS D1).

Commands (version-1 scope): doctor, status, migrate, version.
Later milestones add: secrets, discord, project, backup, startup, run-once.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from agent_roundtable import __version__
from agent_roundtable.config import AppConfig, ConfigurationError, load_config
from agent_roundtable.domain.models import AgentProvider, AgentSeed
from agent_roundtable.observability.health import disk_space_status
from agent_roundtable.observability.logging import setup_logging
from agent_roundtable.persistence.backup import BackupService
from agent_roundtable.persistence.database import Database, FatalDatabaseError
from agent_roundtable.persistence.repositories.agents import AgentRepository

_EXIT_OK = 0
_EXIT_DEGRADED = 2
_EXIT_ERROR = 3


def _first_line(text: str) -> str:
    if not text.strip():
        return "(no output)"
    return text.strip().splitlines()[0].strip()


def _probe_version(argv: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            argv, check=False, capture_output=True, text=True, timeout=30
        )
        if proc.returncode == 0:
            return True, _first_line(proc.stdout or proc.stderr)
        return False, _first_line(
            proc.stderr or proc.stdout or f"exit {proc.returncode}"
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def _check_executable(name: str) -> tuple[bool, str]:
    found = shutil.which(name)
    if not found:
        return False, f"'{name}' executable not found on PATH"
    ok, detail = _probe_version([name, "--version"])
    return ok, f"{found} -> {detail}"


def _ensure_data_dirs(config: AppConfig) -> Path:
    data_dir = config.resolved_data_dir
    for sub in ("config", "database", "artifacts", "logs", "backups", "worktrees"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    return data_dir


def _command_migrate(config: AppConfig) -> int:
    data_dir = _ensure_data_dirs(config)
    db = Database(config.database_path)
    try:
        db.initialize_sync()
        repo = AgentRepository(db)
        import asyncio

        asyncio.run(_seed_agents(repo))
    except FatalDatabaseError as exc:
        print(f"MIGRATE RESULT: FATAL - {exc}")
        return _EXIT_ERROR
    finally:
        import asyncio

        asyncio.run(db.close())
    print(f"MIGRATE RESULT: OK (database at {db.path}, data root {data_dir})")
    return _EXIT_OK


async def _seed_agents(repo: AgentRepository) -> None:
    """Seed agent identities for core entities (non-destructive: works from empty DB)."""
    await repo.ensure_agent(
        AgentSeed(
            agent_id="codex",
            provider=AgentProvider.CODEX,
            display_name="Codex Agent (Architect/Reviewer)",
            persona_path="prompts/codex_architect.md",
        )
    )
    await repo.ensure_agent(
        AgentSeed(
            agent_id="antigravity",
            provider=AgentProvider.ANTIGRAVITY,
            display_name="Antigravity Agent (Engineer/Investigator)",
            persona_path="prompts/antigravity_engineer.md",
        )
    )


async def _command_status_async(config: AppConfig) -> tuple[list[str], bool]:
    lines: list[str] = []
    healthy = True
    data_root = config.resolved_data_dir
    lines.append(f"data root present: {data_root.is_dir()} ({data_root})")
    lines.append(f"startup mode: {config.app.startup_mode}")
    try:
        disk = disk_space_status(
            data_root, config.observability.low_disk_space_threshold_gb
        )
        lines.append(
            f"disk space: {disk.free_gb:.1f} GiB free (warning threshold {disk.threshold_gb} GiB)"
            + ("; LOW SPACE - new project writes paused" if disk.warning else "")
        )
        if disk.warning:
            healthy = False
    except OSError as exc:
        lines.append(f"disk space: unavailable ({type(exc).__name__})")
    db_path = config.database_path
    if not db_path.is_file():
        lines.append(
            f"database: NOT INITIALIZED ({db_path}); run 'agent-roundtable migrate'"
        )
        healthy = False
        return lines, healthy
    db = Database(db_path)
    try:
        await db.initialize()
        agents = await AgentRepository(db).list_agents()
        for row in agents:
            lines.append(
                f"agent {row['provider']}: enabled={bool(row['enabled'])} "
                f"({row['display_name']})"
            )
        lines.append(f"database: {db_path} (WAL, foreign_keys ON)")
        counts = await db.fetch_all(
            "SELECT status,COUNT(*) AS count FROM tasks "
            "WHERE status IN ('queued','interrupted') GROUP BY status"
        )
        task_counts = {row["status"]: int(row["count"]) for row in counts}
        lines.append(
            f"tasks: {task_counts.get('queued', 0)} queued, "
            f"{task_counts.get('interrupted', 0)} interrupted"
        )
        await db.close()
    except FatalDatabaseError as exc:
        lines.append(f"database: FATAL - {exc}")
        healthy = False
    return lines, healthy


def _status_sync(config: AppConfig) -> int:
    try:
        import asyncio

        lines, healthy = asyncio.run(_command_status_async(config))
    except ConfigurationError as exc:
        print(f"STATUS: configuration error - {exc}")
        return _EXIT_ERROR
    for line in lines:
        print(line)
    print(f"STATUS RESULT: {'OK' if healthy else 'NEEDS ATTENTION'}")
    return _EXIT_OK if healthy else _EXIT_DEGRADED


def _command_doctor(config: AppConfig | None) -> int:
    checks: list[tuple[str, str, str]] = []
    import platform

    checks.append(
        (
            "windows",
            "INFO",
            f"{platform.system()} {platform.release()} {platform.version()}",
        )
    )
    ok, detail = _probe_version([sys.executable, "--version"])
    checks.append(("python", "PASS" if ok else "FAIL", detail))
    ok_git, detail_git = _probe_version(["git", "--version"])
    checks.append(("git", "PASS" if ok_git else "FAIL", detail_git))

    if config is not None:
        for key, value in config.describe_sanitized().items():
            checks.append((f"config.{key}", "INFO", str(value)))
        checks.append(("config.validation", "PASS", "schema accepted"))
        if config.app.startup_mode == "windows_logon" and sys.platform == "win32":
            try:
                task_check = subprocess.run(
                    [
                        "schtasks.exe",
                        "/Query",
                        "/TN",
                        "Agent Roundtable Controller",
                        "/FO",
                        "LIST",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                installed = task_check.returncode == 0
                checks.append(
                    (
                        "startup_task",
                        "PASS" if installed else "FAIL",
                        "registered for this account"
                        if installed
                        else "not registered; run scripts\\install-startup-task.ps1",
                    )
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                checks.append(("startup_task", "FAIL", type(exc).__name__))
        elif config.app.startup_mode == "manual":
            checks.append(("startup_task", "INFO", "manual start configured"))
        try:
            disk = disk_space_status(
                config.resolved_data_dir,
                config.observability.low_disk_space_threshold_gb,
            )
            checks.append(
                (
                    "disk_space",
                    "WARN" if disk.warning else "PASS",
                    f"{disk.free_gb:.1f} GiB free; threshold {disk.threshold_gb} GiB",
                )
            )
        except OSError as exc:
            checks.append(("disk_space", "WARN", type(exc).__name__))
    else:
        checks.append(("config", "FAIL", "configuration not loadable"))

    ok_codex, detail_codex = _check_executable("codex")
    checks.append(("codex", "PASS" if ok_codex else "FAIL", detail_codex))

    agy_found = shutil.which("agy") is not None
    if agy_found:
        ok_agy, detail_agy = _check_executable("agy")
        checks.append(("agy", "PASS" if ok_agy else "FAIL", detail_agy))
    else:
        checks.append(("agy", "FAIL", "'agy' not found on PATH (Antigravity CLI)"))
        checks.append(
            (
                "agy_policy",
                "INFO",
                "Antigravity adapter stays DISABLED/SANDBOX_UNVERIFIED until installed+verified",
            )
        )

    if config is not None:
        db_path = config.database_path
        checks.append(
            (
                "database",
                "PASS" if db_path.is_file() else "INFO",
                f"{db_path}"
                + ("" if db_path.is_file() else " (not initialized; run migrate)"),
            )
        )
        if db_path.is_file():
            try:
                import sqlite3

                conn = sqlite3.connect(db_path)
                integrity_rows = conn.execute("PRAGMA quick_integrity_check").fetchall()
                ok_integrity = not integrity_rows or all(
                    str(r[0]).lower() == "ok" for r in integrity_rows
                )
                version_row = conn.execute(
                    "SELECT MAX(version) AS v FROM schema_migrations"
                ).fetchone()
                conn.close()
                checks.append(
                    (
                        "database.integrity",
                        "PASS" if ok_integrity else "FAIL",
                        f"schema version {version_row[0] if version_row else None}",
                    )
                )
            except (sqlite3.Error, OSError) as exc:
                checks.append(("database.integrity", "FAIL", str(exc)))

    for name, status, detail in checks:
        print(f"{name:<28} {status:<7} {detail}")

    failing = [c for c in checks if c[1] == "FAIL"]
    warnings = [c for c in checks if c[1] == "WARN"]
    print(
        f"DOCTOR RESULT: {'PASS' if not failing and not warnings else 'DEGRADED'} "
        f"({len(failing)} failing, {len(warnings)} warning check(s))"
    )
    if config is not None and not agy_found:
        print(
            "Note: Antigravity CLI missing -> documented single-agent (Codex-only) "
            "degraded mode; unattended writes for Antigravity stay disabled (D5)."
        )
    return _EXIT_OK if not failing and not warnings else _EXIT_DEGRADED


def _command_secrets(config: AppConfig, action: str, name: str) -> int:
    from agent_roundtable.secrets.store import resolve_store

    store = resolve_store()
    credential_name = config.discord.credential_name
    if name == "discord-token" and credential_name is None:
        print("error: discord.credential_name is not set in config")
        return _EXIT_ERROR
    try:
        if action == "set":
            import getpass

            value = getpass.getpass(
                f"Enter secret for '{name}' (credential name '{credential_name}', input hidden): "
            )
            if not value.strip():
                print("error: empty secret rejected")
                return _EXIT_ERROR
            store.set(credential_name, value.strip())
            print(
                f"SECRETS RESULT: stored '{name}' under '{credential_name}' "
                f"(existence verified; value never displayed)"
            )
            return _EXIT_OK
        if action == "delete":
            existed = store.delete(credential_name)
            print(
                f"SECRETS RESULT: '{credential_name}' {'deleted' if existed else 'was not present'}"
            )
            return _EXIT_OK
        if action == "status":
            exists = store.get(credential_name) is not None
            print(
                f"SECRETS RESULT: '{credential_name}' {'present' if exists else 'missing'} "
                "(value not displayed by design)"
            )
            return _EXIT_OK
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report, never echo secret content
        print(f"error: secrets operation failed: {exc}")
        return _EXIT_ERROR
    return _EXIT_ERROR  # pragma: no cover


def _command_run(config: AppConfig) -> int:
    import asyncio

    from agent_roundtable.discord.bot import run_controller_sync
    from agent_roundtable.lifecycle import InstanceAlreadyRunning, SingleInstanceLock
    from agent_roundtable.persistence.recovery import (
        mark_controller_stopped,
        recover_after_restart,
    )
    from agent_roundtable.secrets.store import resolve_store
    from agent_roundtable.services import Services

    services = None
    try:
        with SingleInstanceLock():
            services = Services.build(config)
            _ensure_data_dirs(config)
            setup_logging(
                config.resolved_data_dir,
                config.observability.log_level,
                config.observability.redact_secrets,
            )

            async def _prepare() -> None:
                await services.db.initialize()
                counts = await recover_after_restart(services.db)
                logging.getLogger("agent_roundtable.lifecycle").info(
                    "startup recovery completed: %s", str(counts)
                )

            asyncio.run(_prepare())
            exit_code = run_controller_sync(config, services, resolve_store())
            asyncio.run(mark_controller_stopped(services.db, exit_code=exit_code))
            return exit_code
    except InstanceAlreadyRunning:
        print("CONTROLLER RESULT: another instance is already running")
        return _EXIT_DEGRADED
    except Exception as exc:
        logging.getLogger("agent_roundtable.lifecycle").exception(
            "controller terminated unexpectedly"
        )
        if services is not None and services.db.initialized:
            try:
                asyncio.run(mark_controller_stopped(services.db, exit_code=_EXIT_ERROR))
            except Exception:
                logging.getLogger("agent_roundtable.lifecycle").exception(
                    "could not persist controller failure status"
                )
        print(f"CONTROLLER RESULT: stopped with {type(exc).__name__}; see local logs")
        return _EXIT_ERROR
    finally:
        if services is not None and services.db.initialized:
            asyncio.run(services.db.close())


def _command_backup(config: AppConfig, action: str, backup_id: str | None) -> int:
    import asyncio

    from agent_roundtable.lifecycle import InstanceAlreadyRunning, SingleInstanceLock

    db = Database(config.database_path)
    try:
        db.initialize_sync()
        service = BackupService(db, config.backups_dir)
        if action == "create":
            result = asyncio.run(service.create())
            print(f"BACKUP CREATED: {result}")
            print(
                "WARNING: this local backup is stored on the same disk as the data root."
            )
        elif action == "list":
            rows = asyncio.run(service.list())
            for row in rows:
                print(
                    f"{row['backup_id']}  {row['status']}  {row['created_at']}  {row['destination']}"
                )
        elif action == "verify":
            manifest = asyncio.run(service.verify(backup_id or ""))
            print(
                f"BACKUP VERIFIED: {manifest['backup_id']} schema={manifest['schema_version']}"
            )
        elif action == "restore":
            try:
                with SingleInstanceLock():
                    asyncio.run(service.restore(backup_id or ""))
            except InstanceAlreadyRunning as exc:
                raise ValueError("stop the controller before restoring") from exc
            print(f"BACKUP RESTORED: {backup_id} (a pre-restore backup was created)")
        return _EXIT_OK
    except (ValueError, OSError, FatalDatabaseError) as exc:
        print(f"BACKUP ERROR: {exc}")
        return _EXIT_ERROR
    finally:
        if db.initialized:
            asyncio.run(db.close())


def _command_project(config: AppConfig, action: str, args: argparse.Namespace) -> int:
    import asyncio

    from agent_roundtable.projects.registry import ProjectRegistry

    db = Database(config.database_path)
    try:
        db.initialize_sync()
        registry = ProjectRegistry(
            db, config.resolved_worktree_root, config.resolved_data_dir
        )
        if action == "add":
            info = registry.inspect_path(args.path)
            print(
                f"Project: {args.name}\nCanonical repository: {info['path']}\nBranch: {info['branch']}\nHEAD: {info['head']}"
            )
            dirty = any(
                not line.startswith("#") for line in info["status"].splitlines()
            )
            print(f"Working-tree status: {'has local changes' if dirty else 'clean'}")
            if info["warnings"]:
                for warning in info["warnings"]:
                    print(
                        f"WARNING: {warning}; write execution will remain disabled until resolved."
                    )
            answer = input(
                "Type REGISTER to confirm this exact repository path: "
            ).strip()
            if answer != "REGISTER":
                print("PROJECT RESULT: registration canceled")
                return _EXIT_DEGRADED
            project_id = asyncio.run(
                registry.register(args.name, info["path"], confirmed=True)
            )
            print(
                f"PROJECT REGISTERED: {project_id} (writes disabled until owner configures policy)"
            )
        elif action == "list":
            rows = asyncio.run(registry.list())
            for row in rows:
                policy = row["policy"]
                print(
                    f"{row['project_id']}  {row['name']}  enabled={bool(row['enabled'])}  writes={policy.autonomous_write_enabled}  {row['repo_path']}"
                )
        elif action == "doctor":
            row = asyncio.run(registry.get_by_name(args.name))
            if row is None:
                raise ValueError("project not found")
            report = registry.doctor(row)
            print(
                f"PROJECT: {args.name}\nPath: {report['path']}\nBranch: {report['branch']}\nHEAD: {report['head']}\nPolicy: writes={report['policy'].autonomous_write_enabled}; roots={report['policy'].allowed_write_roots}; commands={len(report['policy'].allowed_commands)}"
            )
            dirty = any(
                not line.startswith("#") for line in report["status"].splitlines()
            )
            print(f"Working-tree status: {'has local changes' if dirty else 'clean'}")
            for warning in report["warnings"]:
                print(f"WARNING: {warning}")
        elif action == "policy":
            row = asyncio.run(registry.get_by_name(args.name))
            if row is None:
                raise ValueError("project not found")
            if args.enable_writes and args.disable_writes:
                raise ValueError("choose either --enable-writes or --disable-writes")
            if args.resolve_registration_warnings and args.accept_registration_warnings:
                raise ValueError(
                    "choose either --resolve-registration-warnings or --accept-registration-warnings"
                )
            policy = row["policy"]
            if args.allow_write_root:
                policy.allowed_write_roots = args.allow_write_root
            if args.allow_command:
                policy.allowed_commands = args.allow_command
            if args.allow_agent:
                policy.allowed_agents = args.allow_agent
            if args.resolve_registration_warnings:
                current = registry.doctor(row)["warnings"]
                if current:
                    raise ValueError(
                        "possible credential files remain; resolve or explicitly accept them first"
                    )
                policy.registration_warnings = []
            if args.accept_registration_warnings:
                warnings = registry.doctor(row)["warnings"]
                if not warnings:
                    raise ValueError(
                        "there are no current registration warnings to accept"
                    )
                for warning in warnings:
                    print(f"WARNING EXCEPTION: {warning}")
                answer = input(
                    "Type ACCEPT PROJECT CREDENTIAL WARNINGS to allow this project: "
                ).strip()
                if answer != "ACCEPT PROJECT CREDENTIAL WARNINGS":
                    print("PROJECT RESULT: policy change canceled")
                    return _EXIT_DEGRADED
                policy.registration_exceptions = sorted(
                    set(policy.registration_exceptions + warnings)
                )
                policy.registration_warnings = []
            if args.enable_writes:
                answer = input(
                    "Type ENABLE WRITES to authorize workspace writes under this policy: "
                ).strip()
                if answer != "ENABLE WRITES":
                    print("PROJECT RESULT: policy change canceled")
                    return _EXIT_DEGRADED
                policy.autonomous_write_enabled = True
            if args.allow_local_commit:
                policy.allow_local_commit = True
            if args.disable_writes:
                policy.autonomous_write_enabled = False
            asyncio.run(registry.set_policy(row["project_id"], policy))
            print(
                f"PROJECT POLICY UPDATED: {args.name}; writes={policy.autonomous_write_enabled}; roots={policy.allowed_write_roots}; commands={len(policy.allowed_commands)}"
            )
        return _EXIT_OK
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"PROJECT ERROR: {exc}")
        return _EXIT_ERROR
    finally:
        if db.initialized:
            asyncio.run(db.close())


def _command_task(config: AppConfig, action: str, args: argparse.Namespace) -> int:
    import asyncio
    import getpass
    from uuid import UUID

    from agent_roundtable.services import Services

    services = Services.build(config)
    try:
        services.db.initialize_sync()
        if action == "create":
            project = asyncio.run(services.projects.get_by_name(args.project))
            if project is None:
                raise ValueError("project not found; register it first")
            if args.agent and args.agent not in project["policy"].allowed_agents:
                raise ValueError("requested agent is not eligible under project policy")
            task_id = asyncio.run(
                services.tasks.create(
                    project_id=UUID(project["project_id"]),
                    created_by=getpass.getuser(),
                    title=args.title,
                    goal=args.goal,
                    acceptance=args.acceptance or [],
                    path_scopes=args.path_scope,
                    preferred_writer=args.agent,
                )
            )
            print(
                f"TASK PROPOSED: {task_id}; approve with 'agent-roundtable task approve {task_id}'"
            )
        elif action == "list":
            rows = asyncio.run(services.tasks.list())
            for row in rows:
                print(
                    f"{row['task_id']}  {row['status']}  {row['title']}  project={row['project_id']}"
                    + (
                        f"  worktree={row['worktree_path']}"
                        if row["worktree_path"]
                        else ""
                    )
                )
        elif action == "approve":
            asyncio.run(services.task_service.approve(args.task_id, getpass.getuser()))
            print(f"TASK APPROVED: {args.task_id}")
        elif action == "run":
            result = asyncio.run(services.task_service.run(args.task_id, args.agent))
            print(f"TASK RESULT: {result['status']}")
            if result.get("commit_sha"):
                print(f"LOCAL COMMIT: {result['commit_sha']}")
            if result.get("reason_code"):
                print(f"REASON: {result['reason_code']}")
        elif action == "review":
            result = asyncio.run(services.task_service.review(args.task_id, args.agent))
            print(f"TASK REVIEW: {result['status']} by {result['reviewer_id']}")
            print(result["response"])
        elif action == "complete":
            asyncio.run(services.task_service.complete(args.task_id, getpass.getuser()))
            print(
                f"TASK COMPLETED: {args.task_id} (local commit remains on its task branch)"
            )
        return _EXIT_OK
    except (ValueError, OSError, PermissionError, RuntimeError) as exc:
        print(f"TASK ERROR: {exc}")
        return _EXIT_ERROR
    finally:
        if services.db.initialized:
            asyncio.run(services.db.close())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-roundtable",
        description="Agent Roundtable controller management CLI",
    )
    parser.add_argument(
        "--version", action="version", version=f"agent-roundtable {__version__}"
    )
    parser.add_argument("--config", help="explicit path to config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="environment and configuration diagnostics")
    subparsers.add_parser("status", help="controller/database status overview")
    subparsers.add_parser(
        "migrate", help="initialize/verify database and seed identities"
    )
    subparsers.add_parser(
        "run", help="connect and run the controller (Discord gateway)"
    )

    secrets_parser = subparsers.add_parser(
        "secrets", help="manage stored secrets (Windows Credential Manager)"
    )
    secrets_sub = secrets_parser.add_subparsers(dest="secrets_command", required=True)
    secrets_set = secrets_sub.add_parser(
        "set", help="store a secret (hidden prompt; never echoed)"
    )
    secrets_set.add_argument(
        "name", choices=["discord-token"], help="which secret to set"
    )
    secrets_del = secrets_sub.add_parser("delete", help="remove a stored secret")
    secrets_del.add_argument("name", choices=["discord-token"])
    secrets_stat = secrets_sub.add_parser(
        "status", help="existence check only (value never shown)"
    )
    secrets_stat.add_argument("name", choices=["discord-token"])

    backup_parser = subparsers.add_parser(
        "backup", help="create, verify, list, or restore backups"
    )
    backup_sub = backup_parser.add_subparsers(dest="backup_command", required=True)
    backup_sub.add_parser("create")
    backup_sub.add_parser("list")
    verify_parser = backup_sub.add_parser("verify")
    verify_parser.add_argument("backup_id")
    restore_parser = backup_sub.add_parser("restore")
    restore_parser.add_argument("backup_id")

    project_parser = subparsers.add_parser(
        "project", help="register and inspect owner projects"
    )
    project_sub = project_parser.add_subparsers(dest="project_command", required=True)
    project_add = project_sub.add_parser(
        "add", help="register a Git repository after interactive path confirmation"
    )
    project_add.add_argument("name")
    project_add.add_argument("path")
    project_sub.add_parser("list", help="list registered projects")
    project_doctor = project_sub.add_parser(
        "doctor", help="recheck a registered project"
    )
    project_doctor.add_argument("name")
    project_policy = project_sub.add_parser(
        "policy", help="configure explicit project write policy"
    )
    project_policy.add_argument("name")
    project_policy.add_argument("--enable-writes", action="store_true")
    project_policy.add_argument("--disable-writes", action="store_true")
    project_policy.add_argument("--resolve-registration-warnings", action="store_true")
    project_policy.add_argument("--accept-registration-warnings", action="store_true")
    project_policy.add_argument("--allow-write-root", action="append")
    project_policy.add_argument("--allow-command", action="append", nargs="+")
    project_policy.add_argument(
        "--allow-agent", action="append", choices=["codex", "antigravity"]
    )
    project_policy.add_argument("--allow-local-commit", action="store_true")

    task_parser = subparsers.add_parser(
        "task", help="propose, approve, and run isolated implementation tasks"
    )
    task_sub = task_parser.add_subparsers(dest="task_command", required=True)
    task_create = task_sub.add_parser("create")
    task_create.add_argument("--project", required=True)
    task_create.add_argument("--title", required=True)
    task_create.add_argument("--goal", required=True)
    task_create.add_argument("--path-scope", action="append", required=True)
    task_create.add_argument("--acceptance", action="append")
    task_create.add_argument("--agent", choices=["codex", "antigravity"])
    task_approve = task_sub.add_parser("approve")
    task_approve.add_argument("task_id")
    task_sub.add_parser("list")
    task_run = task_sub.add_parser("run")
    task_run.add_argument("task_id")
    task_run.add_argument("--agent", choices=["codex", "antigravity"], required=True)
    task_review = task_sub.add_parser("review")
    task_review.add_argument("task_id")
    task_review.add_argument("--agent", choices=["codex", "antigravity"], required=True)
    task_complete = task_sub.add_parser("complete")
    task_complete.add_argument("task_id")

    args = parser.parse_args(argv)

    config: AppConfig | None = None
    try:
        config = load_config(args.config)
    except ConfigurationError as exc:
        if args.command == "doctor":
            # doctor must be able to run even without a config file present
            config = None
            print(f"config: FAIL {exc}")
        else:
            print(f"error: {exc}")
            return _EXIT_ERROR

    if args.command == "doctor":
        exit_code = _command_doctor(config)
        if config is not None:
            setup_logging(
                config.resolved_data_dir,
                config.observability.log_level,
                config.observability.redact_secrets,
            )
        return exit_code
    if args.command == "status":
        assert config is not None
        return _status_sync(config)
    if args.command == "migrate":
        assert config is not None
        return _command_migrate(config)
    if args.command == "secrets":
        assert config is not None
        return _command_secrets(config, args.secrets_command, args.name)
    if args.command == "backup":
        assert config is not None
        return _command_backup(
            config, args.backup_command, getattr(args, "backup_id", None)
        )
    if args.command == "project":
        assert config is not None
        return _command_project(config, args.project_command, args)
    if args.command == "task":
        assert config is not None
        return _command_task(config, args.task_command, args)
    if args.command == "run":
        assert config is not None
        return _command_run(config)
    parser.error(f"unknown command: {args.command}")  # pragma: no cover
    return _EXIT_ERROR  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
