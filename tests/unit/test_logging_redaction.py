"""Secret redaction and structured logging (§33.4)."""

from __future__ import annotations

from agent_roundtable.observability.logging import redact


def test_webhook_redacted() -> None:
    text = (
        "failed to post https://discord.com/api/webhooks/1234567890/AbCdEf-GhIjKl notes"
    )
    out = redact(text)
    assert "AbCdEf-GhIjKl" not in out and "1234567890" not in out
    assert "[REDACTED_WEBHOOK]" in out


def test_key_value_secret_redacted() -> None:
    out = redact('discord token="super-secret-value-123" connected')
    assert "super-secret-value-123" not in out
    assert "[REDACTED_SECRET]" in out


def test_bearer_redacted() -> None:
    out = redact("authorization: Bearer example-token-value-abcdef123456")
    assert "example-token-value-abcdef123456" not in out
    assert "authorization=" in out.lower()


def test_ordinary_text_untouched() -> None:
    text = "Task 7f2a created for project sleeping-dogs-web branch roundtable/abc-codex"
    assert redact(text) == text


def test_redaction_does_not_destroy_structure() -> None:
    out = redact('{"memory_key": "vault-9"}')
    assert "[REDACTED_SECRET]" in out
    assert "vault-9" not in out
