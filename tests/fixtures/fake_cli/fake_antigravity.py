"""Fake Antigravity CLI for deterministic tests (§35.3).

Invoked as: <python> fake_antigravity.py -p <prompt> --output-format json.
Scenario via AGENTROUNDTABLE_FAKE_SCENARIO. No credentials, no network.
"""

from __future__ import annotations

import json
import os
import sys
import time

_OK_ENVELOPE = {
    "status": "success",
    "response": "FAKE_ANTIGRAVITY_OK",
    "conversation_id": "FAKE-CONV-1",
    "usage": {"input_tokens": 12, "output_tokens": 4},
}


def main() -> int:
    argv = sys.argv[1:]
    if "-p" not in argv or "--output-format" not in argv:
        sys.stderr.write(
            f"expected -p <prompt> --output-format json; argv={argv[:6]}\n"
        )
        return 99
    for flag in argv:
        if flag == "--dangerously-skip-permissions":
            sys.stderr.write("prohibited flag --dangerously-skip-permissions\n")
            return 97
    scenario = os.environ.get("AGENTROUNDTABLE_FAKE_SCENARIO", "success")

    if scenario == "success":
        sys.stdout.write(json.dumps(_OK_ENVELOPE))
    elif scenario == "error_envelope":
        sys.stdout.write(
            json.dumps(
                {
                    "status": "error",
                    "error": "model unavailable",
                    "conversation_id": "FAKE-CONV-E",
                }
            )
        )
    elif scenario == "auth_expired":
        sys.stdout.write(
            json.dumps(
                {
                    "status": "error",
                    "error": "authentication expired; interactive login required",
                }
            )
        )
    elif scenario == "status_non_success_exit_zero":
        sys.stdout.write(json.dumps({"status": "failed", "error": "internal failure"}))
        return 0  # exit code zero is NOT success (§27.6)
    elif scenario == "soft_denial":
        envelope = dict(_OK_ENVELOPE)
        envelope["status"] = "success"
        envelope["diagnostics"] = [
            "tool 'write_file' is not permitted without interactive approval"
        ]
        sys.stdout.write(json.dumps(envelope))
    elif scenario == "soft_denial_stderr":
        sys.stdout.write(json.dumps(_OK_ENVELOPE))
        sys.stderr.write("permission denied: terminal command requires approval\n")
    elif scenario == "malformed_json":
        sys.stdout.write("{ not json at all ···· truncat")
        return 0
    elif scenario == "timeout_sleeper":
        sys.stdout.write(__import__("json").dumps({"status": "success"}))
        time.sleep(120)
    else:
        sys.stderr.write(f"unknown fake scenario {scenario}\n")
        return 2
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
