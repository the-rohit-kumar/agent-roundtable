"""CLI-level tests: migrate + status via the argv entry point."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_roundtable import cli


@pytest.fixture
def config_in_tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Config at %LOCALAPPDATA%/AgentRoundtable/config/config.yaml within tmp."""
    local_app_data = tmp_path / "locallow" / "localappdata"
    config_dir = local_app_data / "AgentRoundtable" / "config"
    config_dir.mkdir(parents=True)
    src = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"
    (config_dir / "config.yaml").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    return config_dir / "config.yaml"


def test_migrate_then_status(
    config_in_tmp_config_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = cli.main(["--config", str(config_in_tmp_config_dir), "migrate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "MIGRATE RESULT: OK" in out

    rc2 = cli.main(["--config", str(config_in_tmp_config_dir), "status"])
    out2 = capsys.readouterr().out
    assert rc2 in (0, 2)
    assert "STATUS RESULT: OK" in out2
    assert "agent codex" in out2
    assert "agent antigravity" in out2


def test_status_without_migrate_reports_not_initialized(
    config_in_tmp_config_dir: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = cli.main(["--config", str(config_in_tmp_config_dir), "status"])
    out = capsys.readouterr().out
    assert "NOT INITIALIZED" in out
    assert rc == 2


def test_run_once_status_matches(tmp_path: Path) -> None:
    # env var fallback resolution must find the default config location
    local_app_data = tmp_path / "lad"
    cfg_dir = local_app_data / "AgentRoundtable" / "config"
    cfg_dir.mkdir(parents=True)
    src = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"
    (cfg_dir / "config.yaml").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    os.environ["LOCALAPPDATA"] = str(local_app_data)
    # config exists -> no ConfigurationError
    from agent_roundtable.config import load_config

    assert load_config() is not None
