"""Small shared utilities for the package."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    """Timezone-aware UTC ISO-8601 with millisecond precision (§35.1)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")
