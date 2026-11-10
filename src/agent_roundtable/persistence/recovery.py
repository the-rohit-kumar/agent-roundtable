"""Startup reconciliation for work interrupted by a controller exit."""

from __future__ import annotations

import json
from uuid import uuid4

from agent_roundtable.persistence.database import Database
from agent_roundtable.util import utc_now_iso


async def recover_after_restart(db: Database) -> dict[str, int]:
    """Requeue idempotent events and mark in-flight work interrupted atomically."""
    now = utc_now_iso()

    def reconcile(conn):
        previous = conn.execute(
            "SELECT state FROM controller_state WHERE state_id='singleton'"
        ).fetchone()
        unclean_exit = previous is not None and previous["state"] == "running"
        events = conn.execute(
            "UPDATE events SET status='pending',failure_code='controller_recovered' "
            "WHERE status='processing'"
        ).rowcount
        tasks = conn.execute(
            "UPDATE tasks SET status='interrupted',failure_code='controller_restarted',updated_at=? "
            "WHERE status IN ('running','validating')",
            (now,),
        ).rowcount
        task_runs = conn.execute(
            "UPDATE task_runs SET status='interrupted',finished_at=?,summary='controller_restarted' "
            "WHERE status='running'",
            (now,),
        ).rowcount
        agent_runs = conn.execute(
            "UPDATE agent_runs SET status='interrupted',finished_at=? WHERE status='running'",
            (now,),
        ).rowcount
        worktrees = conn.execute(
            "UPDATE worktrees SET status='interrupted' WHERE status='active'"
        ).rowcount
        locks = conn.execute(
            "UPDATE write_locks SET status='released',released_at=? "
            "WHERE status='active'",
            (now,),
        ).rowcount
        counts = {
            "events_requeued": events,
            "tasks_interrupted": tasks,
            "task_runs_interrupted": task_runs,
            "agent_runs_interrupted": agent_runs,
            "worktrees_preserved": worktrees,
            "write_locks_released": locks,
            "previous_exit_unclean": unclean_exit,
        }
        conn.execute(
            "INSERT INTO controller_state(state_id,state,updated_at) VALUES('singleton','running',?) "
            "ON CONFLICT(state_id) DO UPDATE SET state='running',updated_at=excluded.updated_at",
            (now,),
        )
        conn.execute(
            "INSERT INTO audit_events(audit_id,actor_type,actor_id,action,target_type,target_id,"
            "result,correlation_id,created_at,details_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                "system",
                "controller",
                "controller.startup_recovery",
                "controller",
                None,
                "unclean_restart" if unclean_exit else "success",
                str(uuid4()),
                now,
                json.dumps(counts, sort_keys=True),
            ),
        )
        return counts

    return await db.run_transaction(reconcile)


async def mark_controller_stopped(db: Database, *, exit_code: int) -> None:
    """Record a normal or handled-failure exit so the next launch can diagnose it."""
    now = utc_now_iso()
    result = "success" if exit_code == 0 else "failed_exit"

    def stop(conn) -> None:
        conn.execute(
            "INSERT INTO controller_state(state_id,state,updated_at) VALUES('singleton','stopped',?) "
            "ON CONFLICT(state_id) DO UPDATE SET state='stopped',updated_at=excluded.updated_at",
            (now,),
        )
        conn.execute(
            "INSERT INTO audit_events(audit_id,actor_type,actor_id,action,target_type,target_id,"
            "result,correlation_id,created_at,details_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                "system",
                "controller",
                "controller.shutdown",
                "controller",
                None,
                result,
                str(uuid4()),
                now,
                json.dumps({"exit_code": exit_code}, sort_keys=True),
            ),
        )

    await db.run_transaction(stop)
