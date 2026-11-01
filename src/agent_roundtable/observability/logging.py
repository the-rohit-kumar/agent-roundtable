"""Structured logging with correlation IDs and secret redaction (§33.4, §35.1).

- Logs are segmented per UTC day and never automatically deleted (§32.4:
  "old segments must be archived, not automatically deleted" — a daily file with
  no backupCount satisfies this; disk-space policy warns instead of pruning).
- Secret-pattern redaction runs on every emitted message before it reaches the
  file or console.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "roundtable_correlation_id", default="-"
)

# Secret patterns (compiled once). Ordered; first match wins per replacement slot.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Discord webhook URLs
    (
        re.compile(r"https://(?:discord|discordapp)\.com/api/webhooks/[\w]+/[\w\-]+"),
        "https://discord.com/api/webhooks/[REDACTED_WEBHOOK]",
    ),
    # Discord bot tokens (three dot-separated segments, first ~24+ chars)
    (
        re.compile(r"\b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b"),
        "[REDACTED_TOKEN]",
    ),
    # Bearer/Authorization values
    (
        re.compile(
            r"(?i)\b(?:authorization\s*[:=]\s*)?(bearer)\s+[A-Za-z0-9_\-\.=+/]{16,}"
        ),
        "authorization=[REDACTED_TOKEN]",
    ),
    # key=value / JSON-style secret assignments (key adjacent or quote-separated)
    (
        re.compile(
            r"(?i)\b([\w]*(?:token|secret|password|api[_\-]?key|webhook|credential|key)[\w]*)"
            r"(\s*[\"']?\s*[:=]\s*[\"']?)"
            r"(\"[^\"]{4,}\"|'[^']{4,}'|[^\s,;}]{4,})"
        ),
        r"\1\2[REDACTED_SECRET]",
    ),
)


def new_correlation_id() -> str:
    cid = str(uuid.uuid4())
    _correlation_id.set(cid)
    return cid


def set_correlation_id(cid: str) -> None:
    _correlation_id.set(cid)


def current_correlation_id() -> str:
    return _correlation_id.get()


def redact(text: str) -> str:
    result = text
    for pattern, replacement in _SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            try:
                record.args = tuple(
                    redact(str(a)) if isinstance(a, str) else a for a in record.args
                )
            except Exception:  # noqa: BLE001 - defensive: never break logging during redaction
                record.args = None
        return True


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "cid": current_correlation_id(),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    data_dir: Path, level: str = "INFO", redact_secrets: bool = True
) -> None:
    """Configure root logging: console human-readable + JSON daily file."""
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
    )
    if redact_secrets:
        console.addFilter(RedactingFilter())
    root.addHandler(console)

    today = datetime.now(UTC).strftime("%Y%m%d")
    file_handler = TimedRotatingFileHandler(
        logs_dir / f"controller-{today}.log",
        when="midnight",
        backupCount=0,  # keep every rotation: retention is forever (locked decision)
        encoding="utf-8",
    )
    file_handler.setFormatter(StructuredFormatter())
    if redact_secrets:
        file_handler.addFilter(RedactingFilter())
    root.addHandler(file_handler)

    logging.getLogger("agent_roundtable").info(
        "logging initialized (level=%s, daily files in %s)", level.upper(), logs_dir
    )
