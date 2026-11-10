"""Environment probe used by the allowlist test (behavior-driven verification
that the supervisor's environment really contains ONLY allowlisted variables).

Prints JSON of any variable in a hard-coded secret-like list that survived the
allowlist build, plus the names of a few allowlisted ones actually present.

The script prints ONLY variable NAMES (never values) unless --show-values is
passed, which is only used with synthetic fake values in test data.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> int:
    argv = sys.argv[1:]
    show_values = "--show-values" in argv
    secret_like = [
        name
        for name in os.environ
        if any(
            frag in name.upper()
            for frag in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "WEBHOOK")
        )
    ]
    allowed_probe = [
        name
        for name in ("PATH", "TEMP", "APPDATA", "LOCALAPPDATA")
        if name in os.environ
    ]
    payload = {
        "secret_like_present": secret_like
        if not show_values
        else {n: os.environ.get(n, "") for n in secret_like},
        "allowlisted_present": allowed_probe,
        "extra_present": sorted(
            n for n in os.environ if n.startswith("AGENTROUNDTABLE_")
        ),
    }
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
