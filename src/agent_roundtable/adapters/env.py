"""Child-process environment allowlist (§27.4).

A fresh environment is constructed from an explicit allowlist; `os.environ` is
NEVER forwarded wholesale, and any variable whose name contains KEY, TOKEN,
SECRET, PASSWORD, CREDENTIAL, or WEBHOOK is excluded even if an allowlisted
name would otherwise match (the CLI reads its own authenticated account store
under the user profile instead — §27.3).
"""

from __future__ import annotations

import os

#: Documented candidate variables (§27.4). Provider-specific home-directory
#: overrides are added only when captured evidence proves the need — see
#: DECISIONS.md for each addition.
CANDIDATE_VARS: frozenset[str] = frozenset(
    {
        "PATH",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMDRIVE",
    }
)

#: Name fragments that never cross the child boundary (case-insensitive).
FORBIDDEN_FRAGMENTS: tuple[str, ...] = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "WEBHOOK",
)

_PAYLOAD_HOST_VARS: tuple[str, ...] = (
    # documented test-only transports, dropped in production launches
    "AGENTROUNDTABLE_FAKE_SCENARIO",
    "AGENTROUNDTABLE_FAKE_CHILD_FILE",
)

#: Non-secret variables explicitly forwarded for fake-CLI-based tests.
_ALLOWED_EXTRA_FOR_TESTS = frozenset(_PAYLOAD_HOST_VARS)


def _is_forbidden(name: str) -> bool:
    upper = name.upper()
    return any(frag in upper for frag in FORBIDDEN_FRAGMENTS)


def build_child_env(
    *,
    extra_documents: dict[str, str] | None = None,
    include_test_payload_vars: bool = False,
) -> dict[str, str]:
    """Build a minimal environment from current os.environ via the allowlist.

    Case-insensitive Windows lookup; values are passed verbatim (values are not
    secret-like because the allowlist excludes secret-like NAMES).
    """
    lower_lookup = {name.lower(): (name, value) for name, value in os.environ.items()}
    env: dict[str, str] = {}
    for canonical in sorted(CANDIDATE_VARS):
        match = lower_lookup.get(canonical.lower())
        if match is None:
            continue
        name, value = match
        if _is_forbidden(name):
            continue
        env[canonical] = value
    for name, value in sorted((extra_documents or {}).items()):
        if _is_forbidden(name):
            continue
        if not include_test_payload_vars and name in _ALLOWED_EXTRA_FOR_TESTS:
            # test-only transports are stripped from real launches
            continue
        env[name] = value
    return env
