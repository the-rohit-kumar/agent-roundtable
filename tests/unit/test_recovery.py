from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from agent_roundtable.persistence.database import Database
from agent_roundtable.persistence.recovery import (
    mark_controller_stopped,
    recover_after_restart,
)
from agent_roundtable.persistence.repositories.tasks import TaskRepository
from agent_roundtable.util import utc_now_iso


async def test_startup_recovery_requeues_events_and_marks_active_work_interrupted(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "state.sqlite")
    await db.initialize()
    now = utc_now_iso()
    project_id, task_id, event_id, run_id = map(
        str, (uuid4(), uuid4(), uuid4(), uuid4())
    )

    def seed(conn) -> None:
        conn.execute(
            "INSERT INTO projects(project_id,name,repo_path,default_branch,base_visibility,"
            "policy_json,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
            (
                project_id,
                "fixture",
                str(tmp_path / "repo"),
                "main",
                "owner_only",
                "{}",
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO tasks(task_id,project_id,created_by,source,title,goal,acceptance_json,"
            "path_scope_json,risk_level,status,created_at,updated_at,validation_json) "
            "VALUES(?,?,?,'owner','task','goal','[]','[\"src\"]','medium','running',?,?, '{}')",
            (task_id, project_id, "owner", now, now),
        )
        conn.execute(
            "INSERT INTO events(event_id,event_type,source,project_id,dedupe_key,visibility,"
            "payload_json,status,attempt_count,created_at,available_at) "
            "VALUES(?, 'scheduler.tick','scheduler',?,'dedupe','owner_only','{}','processing',1,?,?)",
            (event_id, project_id, now, now),
        )
        conn.execute(
            "INSERT INTO agent_runs(run_id,agent_id,task_id,purpose,status,permission_profile,"
            "started_at,usage_json,diagnostics_json,correlation_id) "
            "VALUES(?, 'codex',?,'implementation','running','sandboxed_write',?,'{}','[]',?)",
            (run_id, task_id, now, str(uuid4())),
        )
        conn.execute(
            "INSERT INTO task_runs(task_run_id,task_id,agent_id,run_id,role,status,started_at,created_at) "
            "VALUES(?,?, 'codex',?,'writer','running',?,?)",
            (str(uuid4()), task_id, run_id, now, now),
        )
        conn.execute(
            "INSERT INTO worktrees(worktree_id,task_id,agent_id,path,branch,base_commit,status,created_at) "
            "VALUES(?,?, 'codex',?,'roundtable/task-codex','abc','active',?)",
            (str(uuid4()), task_id, str(tmp_path / "worktree"), now),
        )
        conn.execute(
            "INSERT INTO write_locks(lock_id,project_id,task_id,path_prefix,agent_id,acquired_at,status) "
            "VALUES(?,?,?,'src','codex',?,'active')",
            (str(uuid4()), project_id, task_id, now),
        )

    await db.run_transaction(seed)
    counts = await recover_after_restart(db)
    assert counts == {
        "events_requeued": 1,
        "tasks_interrupted": 1,
        "task_runs_interrupted": 1,
        "agent_runs_interrupted": 1,
        "worktrees_preserved": 1,
        "write_locks_released": 1,
        "previous_exit_unclean": False,
    }
    assert (
        await db.fetch_one("SELECT status FROM events WHERE event_id=?", (event_id,))
    )["status"] == "pending"
    assert (await db.fetch_one("SELECT status FROM tasks WHERE task_id=?", (task_id,)))[
        "status"
    ] == "interrupted"
    assert (
        await db.fetch_one("SELECT status FROM worktrees WHERE task_id=?", (task_id,))
    )["status"] == "interrupted"
    assert (await TaskRepository(db).list())[0]["worktree_path"] == str(
        tmp_path / "worktree"
    )
    assert (
        await db.fetch_one("SELECT status FROM write_locks WHERE task_id=?", (task_id,))
    )["status"] == "released"
    audit = await db.fetch_one(
        "SELECT action,details_json FROM audit_events WHERE action='controller.startup_recovery'"
    )
    assert audit is not None and "tasks_interrupted" in audit["details_json"]
    second_start = await recover_after_restart(db)
    assert second_start["previous_exit_unclean"] is True
    await mark_controller_stopped(db, exit_code=0)
    state = await db.fetch_one(
        "SELECT state FROM controller_state WHERE state_id='singleton'"
    )
    assert state["state"] == "stopped"
    await db.close()
