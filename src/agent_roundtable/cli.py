"""Local management CLI (§34.3). argparse-based (DECISIONS D1).

Commands (version-1 scope): doctor, status, migrate, version.
Later milestones add: secrets, discord, project, backup, startup, run-once.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from agent_roundtable import __version__
from agent_roundtable.config import AppConfig, ConfigurationError, load_config
from agent_roundtable.domain.models import AgentProvider, AgentSeed
from agent_roundtable.observability.logging import setup_logging
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
    print(
        f"DOCTOR RESULT: {'PASS' if not failing else 'DEGRADED'} "
        f"({len(failing)} failing check(s))"
    )
    if config is not None and not agy_found:
        print(
            "Note: Antigravity CLI missing -> documented single-agent (Codex-only) "
            "degraded mode; unattended writes for Antigravity stay disabled (D5)."
        )
    return _EXIT_OK if not failing else _EXIT_DEGRADED


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
    parser.error(f"unknown command: {args.command}")  # pragma: no cover
    return _EXIT_ERROR  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
