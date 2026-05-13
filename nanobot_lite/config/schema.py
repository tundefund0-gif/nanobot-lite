"""Configuration schema using Pydantic."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class AgentConfig(BaseModel):
    """Agent behavior settings."""
    name: str = "Nanobot-Lite"
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.7
    system_prompt: str = (
        "You are Nanobot-Lite, a helpful AI assistant. "
        "You have access to tools for web search, shell commands, and file operations. "
        "Be concise, helpful, and safe."
    )
    max_turns: int = 50  # max back-and-forth turns per session
    tools_timeout: int = 30  # seconds


class TelegramConfig(BaseModel):
    """Telegram bot settings."""
    enabled: bool = True
    bot_token: str = ""
    allowed_users: list[str] = []  # Telegram user IDs. Empty = allow all.
    admin_user_id: str = ""  # admin user for privileged commands
    reply_to_incoming: bool = True  # reply to user's message in thread


class MemoryConfig(BaseModel):
    """Memory / session persistence settings."""
    enabled: bool = True
    session_dir: Path = Field(default_factory=lambda: Path.home() / ".nanobot_lite" / "sessions")
    max_session_messages: int = 200  # compact after this many messages
    compact_threshold: float = 0.8  # compact when context is 80% full


class LogConfig(BaseModel):
    """Logging settings."""
    level: str = "INFO"
    file: Path | None = Field(default_factory=lambda: Path.home() / ".nanobot_lite" / "nanobot.log")
    max_size_mb: int = 10
    backup_count: int = 3


class ToolsConfig(BaseModel):
    """Tool execution settings."""
    shell_enabled: bool = True
    shell_timeout: int = 30  # max seconds for shell commands
    workspace_dir: Path = Field(default_factory=lambda: Path.home() / "nanobot_workspace")
    restrict_to_workspace: bool = True
    allowed_commands: list[str] = []  # if non-empty, only these commands allowed
    blocked_commands: list[str] = ["rm -rf /", ":(){:|:&};:", "mkfs", "dd if="]  # dangerous commands


class Config(BaseSettings):
    """
    Main configuration for nanobot-lite.
    Loads from ~/.nanobot_lite/config.yaml or environment variables.
    """
    agent: AgentConfig = Field(default_factory=AgentConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    log: LogConfig = Field(default_factory=LogConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    class Config:
        env_prefix = "NANOBOT_"
        env_nested_delimiter = "__"

    def load_from_file(self, path: Path) -> None:
        """Load config from a YAML file, merging with defaults."""
        if not path.exists():
            return
        import yaml
        data = yaml.safe_load(path.read_text())
        if not data:
            return
        # Merge into self
        for section, values in data.items():
            if hasattr(self, section) and isinstance(values, dict):
                section_obj = getattr(self, section)
                for key, value in values.items():
                    if hasattr(section_obj, key):
                        setattr(section_obj, key, value)


def get_default_config_path() -> Path:
    return Path.home() / ".nanobot_lite" / "config.yaml"


def load_config(path: Path | None = None) -> Config:
    """Load configuration from file or environment."""
    cfg = Config()
    if path is None:
        path = get_default_config_path()
    if path.exists():
        cfg.load_from_file(path)
    return cfg


def save_config(cfg: Config, path: Path | None = None) -> None:
    """Save configuration to a YAML file."""
    import yaml
    if path is None:
        path = get_default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "agent": cfg.agent.model_dump(),
        "telegram": cfg.telegram.model_dump(),
        "memory": cfg.memory.model_dump(),
        "log": cfg.log.model_dump(),
        "tools": cfg.tools.model_dump(),
    }

    # Convert Path objects to strings for YAML
    def clean(v: Any) -> Any:
        if isinstance(v, Path):
            return str(v)
        if isinstance(v, dict):
            return {k: clean(val) for k, val in v.items()}
        if isinstance(v, list):
            return [clean(i) for i in v]
        return v

    data = clean(data)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
