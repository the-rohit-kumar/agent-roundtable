"""Fake Codex CLI for deterministic tests (§35.3).

Invoked as: <python> fake_codex.py exec --json --sandbox <x> [-C dir] -
Scenario selected via the AGENTROUNDTABLE_FAKE_SCENARIO environment variable
(forwaded through the supervisor's jest env allowlist for tests only).

Rules: no credentials, no network; argv checks assert the command pipeline
actually produced the required flags (e.g. --no-daemon before exec).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def event(event_type: str, **payload: object) -> str:
    return json.dumps({"type": event_type, **payload}) + "\n"


def _fail(code: int, message: str, argv: list[str]) -> int:
    sys.stderr.write(f"{message}; saw argv={argv[:6]}\n")
    sys.exit(code)


def validate_codex_args(argv: list[str]) -> None:
    """argv starts after the script path: [--no-daemon, exec, ...expected...]."""
    if "--no-daemon" not in argv:
        _fail(98, "missing top-level --no-daemon (DECISIONS D12)", argv)
    if argv.index("--no-daemon") > argv.index("exec"):
        _fail(98, "--no-daemon must be BEFORE 'exec' (top-level, D12)", argv)
    for flag in argv:
        if flag in (
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
        ) or ("--worktree" in flag):
            _fail(97, f"prohibited flag {flag}", argv)


def main() -> int:
    argv = sys.argv[1:]
    validate_codex_args(argv)
    if "-" in argv:
        # consume the entire prompt from stdin (never leave it open)
        sys.stdin.read()

    scenario = os.environ.get("AGENTROUNDTABLE_FAKE_SCENARIO", "success")
    out = sys.stdout
    thread_stub = (
        json.dumps({"type": "thread.started", "thread_id": "FAKE-THREAD-1"}) + "\n"
    )

    if scenario == "success":
        out.write(thread_stub)
        out.write(event("turn.started"))
        out.write(
            event(
                "item.completed",
                item={"id": "item_0", "type": "agent_message", "text": "FAKE_CODEX_OK"},
            )
        )
        out.write(
            event(
                "turn.completed",
                usage={
                    "input_tokens": 100,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 10,
                    "reasoning_output_tokens": 0,
                },
            )
        )

    elif scenario == "failure_exit_nonzero":
        out.write(thread_stub)
        out.write(event("turn.started"))
        out.write(
            event(
                "item.completed",
                item={"id": "i", "type": "agent_message", "text": "FAKE_HALF_RESPONSE"},
            )
        )
        out.write(event("turn.completed", usage={"input_tokens": 1}))
        sys.stderr.write("simulated provider crash\n")
        return 7

    elif scenario == "malformed_json_line":
        out.write(event("turn.started"))
        out.write("this is definitively not json {{{\n")
        out.write(
            event(
                "item.completed",
                item={"id": "i", "type": "agent_message", "text": "FAKE_CODEX_OK"},
            )
        )
        out.write(event("turn.completed", usage={"input_tokens": 2}))

    elif scenario == "zero_exit_no_final":
        # turn.completed alone is NOT a successful run (fail closed)
        out.write(thread_stub)
        out.write(event("turn.started"))
        out.write(event("turn.completed", usage={"input_tokens": 3}))

    elif scenario == "quota_exhausted":
        sys.stderr.write("rate limit exceeded; retry after operator review\n")
        return 1

    elif scenario == "auth_expired":
        sys.stderr.write("authentication expired; please log in interactively\n")
        return 1

    elif scenario in ("large_stdout_stderr", "malformed_unicode"):
        big = "X" * (2_500_000)
        if scenario == "malformed_unicode":
            out.write("\x81\xfe illegal bytes \xff\n")  # replaced on decode
        out.write(thread_stub)
        out.flush()
        # write concurrently on stdout and stderr
        for _ in range(2):
            sys.stderr.write("E" * 1_500_000)
        out.write(big)
        out.write(
            event(
                "item.completed",
                item={"id": "x", "type": "agent_message", "text": "FAKE_CODEX_OK"},
            )
        )
        out.write(event("turn.completed", usage={"input_tokens": 4}))

    elif scenario == "stubborn_grandchild":
        # A stubborn grandchild that outlives the parent; its PID is recorded
        # for the test to verify full-tree kill (Tests §35.3 scenario 10).
        child_file = os.environ.get("AGENTROUNDTABLE_FAKE_CHILD_FILE")
        code = (
            "import time,sys;"
            "open(sys.argv[1],'w').write(str(__import__('os').getpid()));"
            "time.sleep(15)"
        )
        flag = 0
        if sys.platform == "win32":
            flag = (
                0x00000008 | 0x00000200
            )  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [sys.executable, "-c", code, child_file or ""],
            creationflags=flag,
            close_fds=True,
        )
        out.write(thread_stub)
        out.write(event("turn.started"))
        time.sleep(0.3)
        out.write(event("turn.completed", usage={"input_tokens": 5}))
        return 0  # exits "successfully" leaving the orphan behind

    elif scenario == "timeout_sleeper":
        out.write(thread_stub)
        out.write(event("turn.started"))
        time.sleep(120)  # killed by the supervisor timeout, never completes

    elif scenario == "deny_all":
        out.write(thread_stub)
        out.write(event("turn.started"))
        out.write(
            event(
                "item.completed",
                item={
                    "id": "i",
                    "type": "command_execution",
                    "command": "cmd",
                    "aggregated_output": "",
                    "exit_code": 1,
                    "status": "failed",
                },
            )
        )
        out.write(event("turn.completed", usage={"input_tokens": 6}))

    else:
        sys.stderr.write(f"unknown fake scenario {scenario}\n")
        return 2

    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
