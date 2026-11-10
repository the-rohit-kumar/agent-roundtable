"""Project-specific command and action authorization."""

from __future__ import annotations

import asyncio
import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_roundtable.adapters.env import build_child_env
from agent_roundtable.observability.logging import redact
from agent_roundtable.projects.registry import ProjectPolicy

_REMOTE_OR_DANGEROUS = {
    "push",
    "merge",
    "rebase",
    "deploy",
    "publish",
    "release",
    "credential",
    "secret",
    "token",
}


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int
    duration_ms: int
    stdout: str
    stderr: str


class CommandPolicy:
    def __init__(self, policy: ProjectPolicy) -> None:
        self.policy = policy

    def authorize(self, argv: list[str], worktree: Path) -> None:
        if not argv or any(not isinstance(arg, str) or "\x00" in arg for arg in argv):
            raise ValueError("invalid command argument vector")
        executable = Path(argv[0]).name.casefold()
        if executable in {
            "cmd",
            "cmd.exe",
            "powershell",
            "powershell.exe",
            "pwsh",
            "pwsh.exe",
            "bash",
            "sh",
        }:
            raise PermissionError("shell wrappers are not allowed for project commands")
        if executable in _REMOTE_OR_DANGEROUS or any(
            token in {"push", "merge", "deploy", "release"} for token in argv[1:]
        ):
            raise PermissionError("remote and deployment commands are blocked")
        if argv not in self.policy.allowed_commands:
            raise PermissionError("command is not exactly allowlisted for this project")
        if not worktree.is_dir():
            raise ValueError("worktree directory is unavailable")

    async def run(
        self, argv: list[str], worktree: Path, *, timeout: int = 1800
    ) -> CommandResult:
        self.authorize(argv, worktree)
        loop = asyncio.get_running_loop()
        started = loop.time()

        def execute() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                argv,
                cwd=worktree,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=build_child_env(),
            )

        result = await asyncio.to_thread(execute)
        elapsed = int((loop.time() - started) * 1000)
        return CommandResult(
            tuple(argv),
            result.returncode,
            elapsed,
            redact(result.stdout[-100_000:]),
            redact(result.stderr[-50_000:]),
        )


def authorize_task_scope(policy: ProjectPolicy, scopes: list[str]) -> None:
    if policy.registration_warnings:
        raise PermissionError("project registration has unresolved credential warnings")
    if not policy.autonomous_write_enabled:
        raise PermissionError("autonomous writes are disabled by project policy")
    roots = [
        root.replace("\\", "/").strip("/").casefold()
        for root in policy.allowed_write_roots
    ]
    if any(
        not root or root.startswith("/") or ".." in root.split("/") for root in roots
    ):
        raise PermissionError("project write roots must be safe relative paths")
    if not roots or not scopes:
        raise PermissionError(
            "project policy or task has no explicit writable path scope"
        )
    for scope in scopes:
        normalized = scope.replace("\\", "/").strip("/").casefold()
        if (
            not normalized
            or normalized.startswith("/")
            or ".." in normalized.split("/")
        ):
            raise PermissionError("task path scope is not a safe relative path")
        if not any(
            normalized == root or normalized.startswith(root + "/") for root in roots
        ):
            raise PermissionError(
                f"task scope is outside approved write roots: {scope}"
            )
        if any(
            fnmatch.fnmatchcase(normalized, pattern.casefold())
            for pattern in policy.denied_paths
        ):
            raise PermissionError(f"task scope matches denied path policy: {scope}")
    if not policy.allowed_agents:
        raise PermissionError("project has no eligible writer agents")


def verify_task_paths(changed_paths: list[str], scopes: list[str]) -> list[str]:
    normalized_scopes = [
        scope.replace("\\", "/").strip("/").casefold() for scope in scopes
    ]
    return [
        path
        for path in changed_paths
        if not any(
            path.replace("\\", "/").casefold() == scope
            or path.replace("\\", "/").casefold().startswith(scope + "/")
            for scope in normalized_scopes
        )
    ]
