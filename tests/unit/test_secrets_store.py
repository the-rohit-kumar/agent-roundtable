"""Secret store round-trip tests (§13.3, §18 rule 5).

The Windows Credential Manager backend is exercised for existence typing only
(no live credential writes in CI); the environment and .env stores are tested
for full round-trip semantics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_roundtable.secrets.store import (
    DotEnvSecretStore,
    EnvironmentSecretStore,
    resolve_store,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: Any) -> None:
    """Prevent leakage from/to the real user environment."""
    for key in list(__import__("os").environ):
        if key.startswith("AGENTROUNDTABLE_SECRET_"):
            monkeypatch.delenv(key, raising=False)


def test_environment_store_round_trip() -> None:
    store = EnvironmentSecretStore()
    assert store.get("discord-token") is None
    store.set("discord-token", "fake-token-value")
    assert store.get("discord-token") == "fake-token-value"
    assert store.delete("discord-token") is True
    assert store.get("discord-token") is None
    assert store.delete("discord-token") is False


def test_dotenv_store_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config" / ".env"
    path.parent.mkdir(parents=True)
    store = DotEnvSecretStore(path)
    store.set("discord-token", "secret-value")
    assert store.get("discord-token") == "secret-value"
    # value persisted to disk, reload sees it too
    second = DotEnvSecretStore(path)
    assert second.get("discord-token") == "secret-value"
    assert second.delete("discord-token") is True
    assert DotEnvSecretStore(path).get("discord-token") is None
    assert "secret-value" not in path.read_text(encoding="utf-8")


def test_dotenv_store_loads_existing_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nAGENTROUNDTABLE_SECRET_DISCORD_TOKEN='loaded'\n",
        encoding="utf-8",
    )
    store = DotEnvSecretStore(path)
    assert store.get("discord-token") == "loaded"


def test_resolve_store_is_windows_backend_here() -> None:
    # This project targets Windows (§1); Credential Manager is the primary store.
    import sys

    store = resolve_store()
    if sys.platform == "win32":
        from agent_roundtable.secrets.store import WindowsCredentialStore

        assert isinstance(store, WindowsCredentialStore)
        # get() of a surely-absent name must return None, not raise
        assert store.get("AgentRoundtable.__definitely_absent_probe__") is None
