"""Strict config models and loader (ARCHITECTURE §15, §34).

- Secrets are NEVER stored in this YAML: the Discord token lives in Windows
  Credential Manager, referenced by credential name only.
- Only documented environment variables (`%LOCALAPPDATA%`) are expanded, and only
  in path-style values.
- Unknown keys and invalid enum values are rejected (fail closed).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_CONFIG_ENV_VARS = ("LOCALAPPDATA",)
_ENV_VAR_PATTERN = re.compile(r"%(?P<name>[A-Z_][A-Z0-9_]*)%")

RetentionMode = Literal["forever"]
ApprovalPolicy = Literal["owner_approval", "none"]


class ConfigurationError(Exception):
    """Raised when configuration is missing, unreadable, or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AgentProfileSettings(_Strict):
    enabled: bool = True
    executable: str
    persona_file: str
    default_timeout_seconds: int = Field(default=600, ge=5, le=7200)
    discussion_profile: Literal["read_only", "discussion"] = "read_only"
    project_read_profile: Literal["read_only"] = "read_only"
    project_write_profile: Literal["sandboxed_write"] = "sandboxed_write"


class AgentsConfig(_Strict):
    codex: AgentProfileSettings
    antigravity: AgentProfileSettings


class DiscordChannelsConfig(_Strict):
    open_room: str | None = None
    decisions: str | None = None
    owner_operations: str | None = None
    research: str | None = None
    code_reviews: str | None = None
    agent_activity: str | None = None


class DiscordConfig(_Strict):
    guild_id: str | None = None
    owner_user_ids: list[str] = Field(default_factory=list)
    friend_role_ids: list[str] = Field(default_factory=list)
    channels: DiscordChannelsConfig = Field(default_factory=DiscordChannelsConfig)
    credential_name: str = "AgentRoundtable.DiscordBotToken"


class AppSettingsConfig(_Strict):
    name: str = "Agent Roundtable"
    timezone: str = "Asia/Kolkata"
    data_dir: str = "%LOCALAPPDATA%/AgentRoundtable"
    single_instance: bool = True
    startup_mode: Literal["windows_logon", "manual"] = "manual"


class AutonomyConfig(_Strict):
    supported_triggers_enabled: bool = False
    agent_initiated_discussions: bool = False
    agent_discovery_events: bool = False
    configured_schedules: list[str] = Field(default_factory=list)
    max_active_discussions: int = Field(default=2, ge=1, le=16)
    max_processes_per_agent: int = Field(default=1, ge=1, le=8)
    max_write_tasks_per_project: int = Field(default=1, ge=1, le=8)
    duplicate_event_window_minutes: int = Field(default=30, ge=0, le=10080)
    max_turns_per_discussion_cycle: int = Field(default=6, ge=1, le=64)
    retry_auth_failure: bool = False
    retry_quota_failure: bool = False


class ExecutionConfig(_Strict):
    worktree_root: str = "%LOCALAPPDATA%/AgentRoundtable/worktrees"
    auto_run_project_allowlisted_tests: bool = True
    auto_create_local_commit: bool = True
    auto_push: bool = False
    auto_publish_pull_request: bool = False
    auto_merge: bool = False
    require_owner_approval_for: list[str] = Field(
        default_factory=lambda: [
            "git_push",
            "pull_request_publish",
            "protected_branch_merge",
            "dependency_install",
            "external_network_command",
            "destructive_filesystem_action",
            "production_deployment",
        ]
    )
    forbidden: list[str] = Field(
        default_factory=lambda: [
            "credential_access",
            "original_checkout_modification",
            "unrestricted_permission_bypass",
            "unrelated_directory_access",
        ]
    )


class MemoryConfig(_Strict):
    retention: RetentionMode = "forever"
    private_memory_separation: bool = True
    shared_memory_promotion: ApprovalPolicy = "owner_approval"
    preserve_disagreements: bool = True
    automatic_ttl_cleanup: bool = False
    backups_enabled: bool = True
    backup_retention: RetentionMode = "forever"


class ObservabilityConfig(_Strict):
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    redact_secrets: bool = True
    retain_operational_history: RetentionMode = "forever"
    low_disk_space_threshold_gb: int = Field(default=5, ge=1, le=1024)


class AppConfig(_Strict):
    schema_version: int = Field(default=1, ge=1)
    app: AppSettingsConfig = Field(default_factory=AppSettingsConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    agents: AgentsConfig
    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    # --- resolved paths ---------------------------------------------------

    @property
    def resolved_data_dir(self) -> Path:
        return Path(_expand_documented_vars(self.app.data_dir))

    @property
    def database_path(self) -> Path:
        return self.resolved_data_dir / "database" / "roundtable.db"

    @property
    def resolved_worktree_root(self) -> Path:
        return Path(_expand_documented_vars(self.execution.worktree_root))

    @property
    def logs_dir(self) -> Path:
        return self.resolved_data_dir / "logs"

    @property
    def artifacts_dir(self) -> Path:
        return self.resolved_data_dir / "artifacts"

    @property
    def backups_dir(self) -> Path:
        return self.resolved_data_dir / "backups"

    def describe_sanitized(self) -> dict[str, Any]:
        """Safe overview for doctor/status output. Contains no secret material."""
        return {
            "schema_version": self.schema_version,
            "app.name": self.app.name,
            "app.timezone": self.app.timezone,
            "app.data_dir": str(self.resolved_data_dir),
            "app.startup_mode": self.app.startup_mode,
            "discord.guild_id": self.discord.guild_id or "(unset)",
            "discord.owner_user_ids_count": len(self.discord.owner_user_ids),
            "discord.friend_role_ids_count": len(self.discord.friend_role_ids),
            "discord.token_credential_name": self.discord.credential_name,
            "agents.codex.enabled": self.agents.codex.enabled,
            "agents.antigravity.enabled": self.agents.antigravity.enabled,
            "execution.worktree_root": str(self.resolved_worktree_root),
            "memory.retention": self.memory.retention,
            "memory.automatic_ttl_cleanup": self.memory.automatic_ttl_cleanup,
            "observability.log_level": self.observability.log_level,
        }


def _expand_documented_vars(value: str) -> str:
    """Expand ONLY documented environment variables; unknown ones are an error.

    ARCHITECTURE §34.1: "Expand only documented environment variables in path
    values." Unknown %VAR% patterns fail closed rather than silently expanding.
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in _CONFIG_ENV_VARS:
            raise ConfigurationError(
                f"Undocumented environment variable '%{name}%' in config path value "
                "(allowed: %LOCALAPPDATA%)"
            )
        actual = os.environ.get(name)
        if actual is None:
            raise ConfigurationError(f"Environment variable '{name}' is not set")
        return actual

    return _ENV_VAR_PATTERN.sub(_replace, value)


def config_search_path(explicit: str | Path | None = None) -> Path:
    """Resolve the config file location: explicit > env override > default."""
    if explicit is not None:
        return Path(explicit)
    env_override = os.environ.get("ROUNDTABLE_CONFIG")
    if env_override:
        return Path(env_override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "AgentRoundtable" / "config" / "config.yaml"


def _prepare_tree(data: Any) -> Any:
    """Recursively expand documented %-vars in string leaves before validation."""
    if isinstance(data, str):
        if "%" in data:
            return _expand_documented_vars(data)
        return data
    if isinstance(data, dict):
        return {k: _prepare_tree(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_prepare_tree(v) for v in data]
    return data


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load and validate the YAML configuration. Raises ConfigurationError."""
    config_path = config_search_path(path)
    if not config_path.is_file():
        raise ConfigurationError(
            f"Configuration file not found: {config_path} "
            "(copy config/config.example.yaml there and fill in Discord IDs)"
        )
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:  # pragma: no cover - filesystem edge
        raise ConfigurationError(f"Configuration file unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("Configuration root must be a YAML mapping")
    prepared = _prepare_tree(raw)
    try:
        return AppConfig.model_validate(prepared)
    except ValidationError as exc:
        # Do not echo offending values back (potential secret-adjacent content).
        locations = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['type']}"
            for err in exc.errors()
        )
        raise ConfigurationError(
            f"Configuration validation failed -> {locations}"
        ) from exc
