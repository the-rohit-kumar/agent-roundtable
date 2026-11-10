"""Canonical project registration and conservative project policy."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso

_SECRET_NAME = re.compile(
    r"(^|[/\\])(?:\.env(?:\..*)?|[^/\\]*(?:secret|token|credential|private.?key)[^/\\]*|[^/\\]*\.(?:pem|p12|key))$",
    re.IGNORECASE,
)


class ProjectPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autonomous_write_enabled: bool = False
    allowed_agents: list[str] = Field(default_factory=lambda: ["codex", "antigravity"])
    allowed_write_roots: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(
        default_factory=lambda: [
            ".env",
            ".env.*",
            "secrets/**",
            "credentials/**",
            "**/*token*",
            "**/*secret*",
            "**/*.pem",
            "**/*.p12",
            "**/*.key",
            ".git/**",
        ]
    )
    allowed_commands: list[list[str]] = Field(default_factory=list)
    allow_dependency_install: bool = False
    allow_network: bool = False
    allow_local_commit: bool = False
    require_review_agent: bool = True
    registration_warnings: list[str] = Field(default_factory=list)
    registration_exceptions: list[str] = Field(default_factory=list)


class ProjectRegistry:
    def __init__(
        self, db: Database, worktree_root: Path, data_root: Path | None = None
    ) -> None:
        self.db = db
        self.worktree_root = Path(worktree_root).resolve()
        self.data_root = Path(data_root).resolve() if data_root is not None else None
        if self.data_root is not None:
            try:
                self.worktree_root.relative_to(self.data_root)
            except ValueError as exc:
                raise ValueError(
                    "managed worktree root must be inside the Roundtable data root"
                ) from exc
        self.controller_root = Path(__file__).resolve().parents[3]

    def inspect_path(self, raw_path: str | Path) -> dict[str, Any]:
        path = Path(raw_path).expanduser()
        if not path.exists() or not path.is_dir():
            raise ValueError("project path must exist and be a directory")
        for parent in (path, *path.parents):
            if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
                raise ValueError("project path cannot traverse a symlink or junction")
        canonical = path.resolve(strict=True)
        try:
            canonical.relative_to(self.controller_root)
        except ValueError:
            pass
        else:
            raise ValueError(
                "the Agent Roundtable controller repository cannot be registered as a target project"
            )
        if canonical == self.controller_root:
            raise ValueError(
                "the controller repository cannot be registered as a target project"
            )
        try:
            canonical.relative_to(self.worktree_root)
        except ValueError:
            pass
        else:
            raise ValueError(
                "project checkout cannot be inside the managed worktree root"
            )
        try:
            self.worktree_root.relative_to(canonical)
        except ValueError:
            pass
        else:
            raise ValueError(
                "managed worktree root cannot be inside the project checkout"
            )
        top = self._git(["rev-parse", "--show-toplevel"], canonical).strip()
        top_path = Path(top).resolve(strict=True)
        if top_path != canonical:
            raise ValueError("register the Git repository root, not a nested directory")
        branch = self._git(["branch", "--show-current"], canonical).strip()
        head = self._git(["rev-parse", "HEAD"], canonical).strip()
        status = self._git(["status", "--porcelain=v2", "--branch"], canonical)
        tracked = self._git(["ls-files"], canonical).splitlines()
        warnings = [
            f"possible credential file: {name}"
            for name in tracked
            if _SECRET_NAME.search(name)
        ]
        return {
            "path": str(canonical),
            "branch": branch or "HEAD",
            "head": head,
            "status": status,
            "warnings": warnings,
        }

    @staticmethod
    def _git(args: list[str], cwd: Path) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"Git inspection failed: {type(exc).__name__}") from exc
        if result.returncode:
            raise ValueError(f"Git inspection failed: {result.stderr.strip()[:300]}")
        return result.stdout

    async def register(
        self, name: str, raw_path: str | Path, *, confirmed: bool
    ) -> UUID:
        if not confirmed:
            raise ValueError(
                "owner confirmation is required to register a project path"
            )
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("project name must contain 1 to 100 characters")
        info = self.inspect_path(raw_path)
        project_id, now = uuid4(), utc_now_iso()
        policy = ProjectPolicy(registration_warnings=info["warnings"])
        await self.db.execute(
            """INSERT INTO projects(project_id,name,repo_path,default_branch,base_visibility,
               policy_json,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)""",
            (
                str(project_id),
                name,
                info["path"],
                info["branch"],
                "owner_only",
                policy.model_dump_json(),
                now,
                now,
            ),
        )
        return project_id

    async def list(self) -> list[dict[str, Any]]:
        rows = await self.db.fetch_all(
            "SELECT * FROM projects ORDER BY name COLLATE NOCASE"
        )
        for row in rows:
            row["policy"] = ProjectPolicy.model_validate_json(row["policy_json"])
        return rows

    async def get(self, project_id: str | UUID) -> dict[str, Any] | None:
        row = await self.db.fetch_one(
            "SELECT * FROM projects WHERE project_id=?", (str(project_id),)
        )
        if row:
            row["policy"] = ProjectPolicy.model_validate_json(row["policy_json"])
        return row

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        row = await self.db.fetch_one(
            "SELECT * FROM projects WHERE name=? COLLATE NOCASE", (name,)
        )
        if row:
            row["policy"] = ProjectPolicy.model_validate_json(row["policy_json"])
        return row

    async def set_policy(self, project_id: str | UUID, policy: ProjectPolicy) -> None:
        await self.db.execute(
            "UPDATE projects SET policy_json=?,updated_at=? WHERE project_id=?",
            (policy.model_dump_json(), utc_now_iso(), str(project_id)),
        )

    def doctor(self, project: dict[str, Any]) -> dict[str, Any]:
        info = self.inspect_path(project["repo_path"])
        expected = str(Path(project["repo_path"]).resolve())
        if info["path"] != expected:
            raise ValueError("registered project canonical path changed")
        return {
            **info,
            "policy": project["policy"],
            "enabled": bool(project["enabled"]),
        }
