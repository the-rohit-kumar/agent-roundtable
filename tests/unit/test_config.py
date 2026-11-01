"""Configuration loader validation (ARCHITECTURE §15, §34)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_roundtable.config import AppConfig, ConfigurationError, load_config


def test_example_config_loads(
    example_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\example\AppData\Local")
    config = load_config(example_config_path)
    assert config.agents.codex.executable == "codex"
    assert config.agents.antigravity.executable == "agy"
    assert config.memory.retention == "forever"
    assert config.memory.automatic_ttl_cleanup is False
    assert (
        config.resolved_data_dir
        == Path(r"C:\Users\example\AppData\Local") / "AgentRoundtable"
    )


def test_unknown_keys_rejected(
    example_config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\x")
    text = example_config_path.read_text(encoding="utf-8")
    bad = text.replace("schema_version: 1", "schema_version: 1\nbogus_key: true")
    tmpfile = Path(example_config_path).with_name("bad-config.yaml")
    tmpfile.write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="bogus_key"):
        load_config(tmpfile)


def test_invalid_enum_rejected(example_config_path: Path) -> None:
    text = example_config_path.read_text(encoding="utf-8")
    bad = text.replace("retention: forever", "retention: 30days")
    tmpfile = Path(example_config_path).with_name("bad-retention.yaml")
    tmpfile.write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="retention"):
        load_config(tmpfile)


def test_undocumented_env_var_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "memory:\n  retention: forever\nexecution:\n"
        '  worktree_root: "%APPDATA%/roundtable-wt"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Undocumented environment variable"):
        load_config(cfg)


def test_deep_defaults_fill() -> None:
    cfg = AppConfig.model_validate(
        {
            "agents": {
                "codex": {"executable": "codex", "persona_file": "p.md"},
                "antigravity": {"executable": "agy", "persona_file": "p.md"},
            }
        }
    )
    assert cfg.memory.retention == "forever"
    assert cfg.autonomy.max_turns_per_discussion_cycle == 6
    assert cfg.execution.auto_push is False
    assert "git_push" in cfg.execution.require_owner_approval_for
    describe = cfg.describe_sanitized()
    assert "discord.guild_id" in describe
    assert describe["memory.automatic_ttl_cleanup"] is False
