from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest

from agent_roundtable.domain.models import (
    AgentCapabilities,
    AgentRunResult,
    AgentRunStatus,
)
from agent_roundtable.execution.policy import CommandPolicy, authorize_task_scope
from agent_roundtable.execution.task_service import TaskService
from agent_roundtable.execution.worktree_manager import WorktreeManager
from agent_roundtable.execution.write_locks import WriteLockRepository, scopes_overlap
from agent_roundtable.persistence.database import Database
from agent_roundtable.persistence.repositories.approvals import ApprovalRepository
from agent_roundtable.persistence.repositories.runs import AgentRunRepository
from agent_roundtable.persistence.repositories.tasks import TaskRepository
from agent_roundtable.projects.registry import ProjectPolicy, ProjectRegistry


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def make_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    git(path, "config", "user.name", "Test Owner")
    git(path, "config", "user.email", "owner@example.invalid")
    (path / "src").mkdir()
    (path / "src" / "base.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(path, "add", "--all")
    git(path, "commit", "-m", "initial")
    return path.resolve()


async def test_project_registration_requires_confirmation_and_rejects_controller_repo(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "target")
    db = Database(tmp_path / "state.sqlite")
    await db.initialize()
    registry = ProjectRegistry(db, tmp_path / "worktrees")
    actual_controller = registry.controller_root
    registry.controller_root = tmp_path / "controller-repository"
    info = registry.inspect_path(repo)
    assert info["head"] == git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="confirmation"):
        await registry.register("target", repo, confirmed=False)
    project_id = await registry.register("target", repo, confirmed=True)
    assert (await registry.get(project_id))["repo_path"] == str(repo)
    registry.controller_root = actual_controller
    with pytest.raises(ValueError, match="controller repository"):
        registry.inspect_path(Path(__file__).resolve().parents[2])
    await db.close()


async def test_worktrees_are_separate_and_write_locks_serialize_overlap(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "target")
    manager = WorktreeManager(tmp_path / "worktrees")
    task1, task2 = "task-one", "task-two"
    db = Database(tmp_path / "locks.sqlite")
    await db.initialize()
    registry = ProjectRegistry(db, tmp_path / "worktrees")
    registry.controller_root = tmp_path / "controller-repository"
    project = str(await registry.register("target", repo, confirmed=True))
    first = manager.create(
        project_id=project, repo_path=repo, task_id=task1, agent_id="codex"
    )
    assert first.path != repo and git(repo, "status", "--porcelain") == ""
    (first.path / "src" / "new.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert manager.verify_scope(first, ProjectPolicy(allowed_write_roots=["src"])) == []
    assert manager.verify_scope(first, ProjectPolicy(allowed_write_roots=["tests"]))
    assert scopes_overlap("src/", "src/api/file.py")
    assert not scopes_overlap("src/api/", "docs/")

    locks = WriteLockRepository(db)
    assert await locks.acquire(project, task1, "codex", ["src/"])
    assert not await locks.acquire(project, task2, "antigravity", ["src/api/"])
    assert await locks.acquire(project, task2, "antigravity", ["docs/"])
    await locks.release(task1)
    await locks.release(task2)
    await db.close()


async def test_command_policy_is_exact_and_blocks_remote_commands(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    policy = ProjectPolicy(allowed_commands=[[sys.executable, "-c", "print('ok')"]])
    result = await CommandPolicy(policy).run(policy.allowed_commands[0], worktree)
    assert result.exit_code == 0 and "ok" in result.stdout
    with pytest.raises(PermissionError, match="allowlisted"):
        await CommandPolicy(policy).run(
            [sys.executable, "-c", "print('other')"], worktree
        )
    with pytest.raises(PermissionError, match="remote"):
        CommandPolicy(policy).authorize(["git", "push"], worktree)
    with pytest.raises(PermissionError, match="scope"):
        authorize_task_scope(
            ProjectPolicy(autonomous_write_enabled=True, allowed_write_roots=["src"]),
            ["docs"],
        )


class WritingAdapter:
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(agent_id="codex", sandboxed_write_supported=True)

    async def run(self, request) -> AgentRunResult:
        target = Path(request.working_directory) / "src" / "generated.py"
        target.write_text("GENERATED = True\n", encoding="utf-8")
        return AgentRunResult(
            run_id=request.run_id,
            agent_id=request.agent_id,
            status=AgentRunStatus.SUCCESS,
            response_text="Implemented in worktree.",
        )


class ReviewingAdapter:
    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(agent_id="antigravity", read_only_supported=True)

    async def run(self, request) -> AgentRunResult:
        return AgentRunResult(
            run_id=request.run_id,
            agent_id=request.agent_id,
            status=AgentRunStatus.SUCCESS,
            response_text="[APPROVED] The change meets the acceptance criteria.",
        )


async def test_task_run_validates_and_commits_only_inside_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "target")
    db = Database(tmp_path / "state.sqlite")
    await db.initialize()
    registry = ProjectRegistry(db, tmp_path / "worktrees")
    registry.controller_root = tmp_path / "controller-repository"
    project_id = await registry.register("target", repo, confirmed=True)
    policy = ProjectPolicy(
        autonomous_write_enabled=True,
        allowed_agents=["codex"],
        allowed_write_roots=["src"],
        allowed_commands=[[sys.executable, "-c", "print('tests passed')"]],
        allow_local_commit=True,
        registration_warnings=[],
    )
    await registry.set_policy(project_id, policy)
    tasks = TaskRepository(db)
    task_id = await tasks.create(
        project_id=UUID(str(project_id)),
        created_by="owner",
        title="Add generated file",
        goal="Create one generated source file",
        acceptance=["file exists"],
        path_scopes=["src"],
        preferred_writer="codex",
    )
    approvals = ApprovalRepository(db)
    service = TaskService(
        db=db,
        projects=registry,
        tasks=tasks,
        approvals=approvals,
        locks=WriteLockRepository(db),
        worktrees=WorktreeManager(tmp_path / "worktrees"),
        runs=AgentRunRepository(db),
        adapters={"codex": WritingAdapter()},
    )
    await service.approve(task_id, "owner")
    policy.autonomous_write_enabled = False
    await registry.set_policy(project_id, policy)
    with pytest.raises(PermissionError, match="writes are disabled"):
        await service.run(task_id, "codex")
    policy.autonomous_write_enabled = True
    policy.registration_warnings = ["possible credential file: .env"]
    await registry.set_policy(project_id, policy)
    with pytest.raises(PermissionError, match="registration warnings"):
        await service.run(task_id, "codex")
    policy.registration_warnings = []
    await registry.set_policy(project_id, policy)
    from agent_roundtable.execution import task_service as task_service_module
    from agent_roundtable.observability.health import DiskSpaceStatus

    monkeypatch.setattr(
        task_service_module,
        "disk_space_status",
        lambda *_args: DiskSpaceStatus(0.5, True, 5),
    )
    service.data_root = tmp_path
    with pytest.raises(PermissionError, match="disk space"):
        await service.run(task_id, "codex")
    service.data_root = None
    monkeypatch.undo()
    result = await service.run(task_id, "codex")
    assert result["status"] == "committed_local"
    assert result["commit_sha"] == git(result["worktree"], "rev-parse", "HEAD")
    assert not (repo / "src" / "generated.py").exists()
    assert git(repo, "status", "--porcelain") == ""
    task = await tasks.get(task_id)
    assert task["status"] == "committed_local"
    assert task["validation"]["commands"][0]["exit_code"] == 0
    assert task["validation"]["owner_review"] == "pending"
    await db.close()


async def test_failed_required_check_prevents_local_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "target")
    db = Database(tmp_path / "state.sqlite")
    await db.initialize()
    registry = ProjectRegistry(db, tmp_path / "worktrees")
    registry.controller_root = tmp_path / "controller-repository"
    project_id = await registry.register("target", repo, confirmed=True)
    policy = ProjectPolicy(
        autonomous_write_enabled=True,
        allowed_agents=["codex"],
        allowed_write_roots=["src"],
        allowed_commands=[[sys.executable, "-c", "raise SystemExit(2)"]],
        allow_local_commit=True,
        registration_warnings=[],
    )
    await registry.set_policy(project_id, policy)
    tasks = TaskRepository(db)
    task_id = await tasks.create(
        project_id=UUID(str(project_id)),
        created_by="owner",
        title="Fail test",
        goal="Exercise failed validation",
        acceptance=[],
        path_scopes=["src"],
        preferred_writer="codex",
    )
    service = TaskService(
        db=db,
        projects=registry,
        tasks=tasks,
        approvals=ApprovalRepository(db),
        locks=WriteLockRepository(db),
        worktrees=WorktreeManager(tmp_path / "worktrees"),
        runs=AgentRunRepository(db),
        adapters={"codex": WritingAdapter()},
    )
    await service.approve(task_id, "owner")
    result = await service.run(task_id, "codex")
    assert result["status"] == "needs_changes"
    assert (await tasks.get(task_id))["commit_sha"] is None
    assert git(repo, "status", "--porcelain") == ""
    original_worktree = result["worktree"]
    policy.allowed_commands = [[sys.executable, "-c", "print('fixed')"]]
    await registry.set_policy(project_id, policy)
    retry = await service.run(task_id, "codex")
    assert retry["status"] == "committed_local"
    assert retry["worktree"] == original_worktree
    await db.close()


async def test_independent_review_and_owner_acceptance_are_required(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "target")
    db = Database(tmp_path / "state.sqlite")
    await db.initialize()
    registry = ProjectRegistry(db, tmp_path / "worktrees")
    registry.controller_root = tmp_path / "controller-repository"
    project_id = await registry.register("target", repo, confirmed=True)
    policy = ProjectPolicy(
        autonomous_write_enabled=True,
        allowed_agents=["codex"],
        allowed_write_roots=["src"],
        allowed_commands=[[sys.executable, "-c", "print('tests passed')"]],
        allow_local_commit=True,
        registration_warnings=[],
    )
    await registry.set_policy(project_id, policy)
    tasks = TaskRepository(db)
    task_id = await tasks.create(
        project_id=UUID(str(project_id)),
        created_by="owner",
        title="Add generated file",
        goal="Create one generated source file",
        acceptance=["file exists"],
        path_scopes=["src"],
        preferred_writer="codex",
    )
    service = TaskService(
        db=db,
        projects=registry,
        tasks=tasks,
        approvals=ApprovalRepository(db),
        locks=WriteLockRepository(db),
        worktrees=WorktreeManager(tmp_path / "worktrees"),
        runs=AgentRunRepository(db),
        adapters={"codex": WritingAdapter(), "antigravity": ReviewingAdapter()},
    )
    await service.approve(task_id, "owner")
    committed = await service.run(task_id, "codex")
    assert committed["status"] == "committed_local"
    with pytest.raises(PermissionError, match="independent agent"):
        await service.review(task_id, "codex")
    with pytest.raises(PermissionError, match="independent reviewer"):
        await service.complete(task_id, "owner")
    review = await service.review(task_id, "antigravity")
    assert review["status"] == "approved"
    await service.complete(task_id, "owner")
    assert (await tasks.get(task_id))["status"] == "completed"
    await db.close()
