"""Controller-owned Git worktrees; never execute in the registered checkout."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from agent_roundtable.projects.registry import ProjectPolicy


@dataclass(frozen=True)
class ManagedWorktree:
    project_id: str
    task_id: str
    agent_id: str
    path: Path
    branch: str
    base_commit: str


class WorktreeManager:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode:
            raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()[:500]}")
        return result.stdout.strip()

    def create(
        self,
        *,
        project_id: str,
        repo_path: Path,
        task_id: str,
        agent_id: str,
        base_commit: str | None = None,
    ) -> ManagedWorktree:
        repo = Path(repo_path).resolve(strict=True)
        if not repo.is_dir():
            raise ValueError("registered project checkout is unavailable")
        try:
            self.root.relative_to(repo)
        except ValueError:
            pass
        else:
            raise ValueError(
                "managed worktree root must be outside the project checkout"
            )
        if agent_id not in {"codex", "antigravity"}:
            raise ValueError("unknown writer agent")
        base = base_commit or self._git(repo, "rev-parse", "HEAD")
        # Verify the supplied revision belongs to this repository before use.
        base = self._git(repo, "rev-parse", "--verify", f"{base}^{{commit}}")
        project_key = str(project_id).replace("-", "")
        task_key = str(task_id).replace("-", "")
        if not all(c.isalnum() for c in project_key + task_key):
            raise ValueError("project/task id contains unsafe characters")
        branch = f"roundtable/{task_key[:12]}-{agent_id}"
        path = self.root / project_key / f"{task_key[:16]}-{agent_id}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError("task worktree path already exists")
        path.parent.resolve().relative_to(self.root)
        self._git(repo, "worktree", "add", "-b", branch, str(path), base)
        canonical = path.resolve(strict=True)
        try:
            canonical.relative_to(self.root)
        except ValueError as exc:
            raise RuntimeError("Git worktree resolved outside managed root") from exc
        actual_root = Path(
            self._git(canonical, "rev-parse", "--show-toplevel")
        ).resolve()
        actual_branch = self._git(canonical, "branch", "--show-current")
        if actual_root != canonical or actual_branch != branch:
            raise RuntimeError("created worktree identity verification failed")
        return ManagedWorktree(
            str(project_id), str(task_id), agent_id, canonical, branch, base
        )

    def reopen(
        self,
        *,
        project_id: str,
        task_id: str,
        agent_id: str,
        path: Path,
        branch: str,
        base_commit: str,
    ) -> ManagedWorktree:
        canonical = Path(path).resolve(strict=True)
        try:
            canonical.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("stored worktree path escapes the managed root") from exc
        actual_root = Path(
            self._git(canonical, "rev-parse", "--show-toplevel")
        ).resolve()
        actual_branch = self._git(canonical, "branch", "--show-current")
        if actual_root != canonical or actual_branch != branch:
            raise ValueError("stored worktree identity no longer matches its task")
        return ManagedWorktree(
            project_id, task_id, agent_id, canonical, branch, base_commit
        )

    def changed_paths(self, worktree: ManagedWorktree) -> list[str]:
        tracked = self._git(worktree.path, "diff", "--name-only", "HEAD").splitlines()
        untracked = self._git(
            worktree.path, "ls-files", "--others", "--exclude-standard"
        ).splitlines()
        staged = self._git(
            worktree.path, "diff", "--cached", "--name-only"
        ).splitlines()
        return sorted(set(tracked + untracked + staged), key=str.casefold)

    def verify_scope(
        self, worktree: ManagedWorktree, policy: ProjectPolicy
    ) -> list[str]:
        violations: list[str] = []
        roots = [
            root.replace("\\", "/").strip("/").casefold()
            for root in policy.allowed_write_roots
        ]
        for raw in self.changed_paths(worktree):
            normalized = raw.replace("\\", "/").casefold()
            if not roots or not any(
                normalized == root or normalized.startswith(root + "/")
                for root in roots
            ):
                violations.append(f"outside_write_roots:{raw}")
            if any(
                fnmatchcase(normalized, pattern.replace("\\", "/").casefold())
                for pattern in policy.denied_paths
            ):
                violations.append(f"denied_path:{raw}")
        return violations

    def remove(self, worktree: ManagedWorktree) -> None:
        # Explicit cleanup only for clean worktrees; retain any owner-review work.
        if self.changed_paths(worktree):
            raise ValueError("worktree has changes; refusing cleanup")
        repo = (
            Path(
                self._git(
                    worktree.path,
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                )
            )
            .resolve()
            .parent
        )
        self._git(repo, "worktree", "remove", str(worktree.path))
        self._git(repo, "branch", "-d", worktree.branch)
