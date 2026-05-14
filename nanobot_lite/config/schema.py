"""Configuration schema — pure Python, no pydantic (no Rust deps)."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ─── Helper: load/save YAML ───────────────────────────────────────────────

def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML file, return empty dict if missing."""
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _save_yaml(data: dict[str, Any], path: Path) -> None:
    """Save dict to YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, default_flow_style=False, sort_keys=False))


# ─── Tool limits ──────────────────────────────────────────────────────────

@dataclass
class ToolLimits:
    shell_timeout: int = 30
    restrict_to_workspace: bool = True
    allowed_commands: list[str] = field(default_factory=list)
    blocked_commands: list[str] = field(default_factory=lambda: [
        "forkbomb", ":(){:|:&}:;",  # fork bombs
        "rm -rf /", "rm -rf /*", "rm -rf ~",  # dangerous rm
        "dd if=/dev/zero of=",  # disk wipe
        "> /dev/sda",  # raw disk write
        "mkfs", "umount /",  # filesystem
        "chmod -R 777 /", "chmod 000 /",  # perms
        "wget http", "curl http",  # download+exec (allow with URL)
        "python -c", "python3 -c",  # inline code exec
        "node -e", "ruby -e", "perl -e",  # other interpreters
        "bash -i", "/dev/null",  # interactive/shell escape
    ])


# ─── Config objects ───────────────────────────────────────────────────────

@dataclass
class ToolsConfig:
    workspace_dir: Path = field(default_factory=lambda: Path.home() / "nanobot_workspace")
    shell_enabled: bool = True
    limits: ToolLimits = field(default_factory=ToolLimits)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolsConfig":
        limits_data = d.get("limits", {})
        if isinstance(limits_data, dict):
            limits = ToolLimits(**limits_data)
        else:
            limits = ToolLimits()

        # Handle workspace_dir path
        raw = d.get("workspace_dir", str(Path.home() / "nanobot_workspace"))
        if isinstance(raw, str):
            wpath = Path(raw).expanduser().resolve()
        else:
            wpath = Path.home() / "nanobot_workspace"

        return cls(
            workspace_dir=wpath,
            shell_enabled=d.get("shell_enabled", True),
            limits=limits,
        )


@dataclass
class TelegramConfig:
    enabled: bool = True
    bot_token: str = ""
    admin_user_id: str = ""
    allowed_users: list[str] = field(default_factory=list)
    reply_to_incoming: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TelegramConfig":
        return cls(
            enabled=d.get("enabled", True),
            bot_token=d.get("bot_token", ""),
            admin_user_id=d.get("admin_user_id", ""),
            allowed_users=d.get("allowed_users", []),
            reply_to_incoming=d.get("reply_to_incoming", True),
        )


@dataclass
class AgentConfig:
    name: str = "Nanobot-Lite"
    provider: str = "opencode-zen"  # "opencode-zen" | "anthropic"
    model: str = "minimax-m2.5-free"
    base_url: str = ""  # e.g. "https://opencode.ai/zen" (opencode-zen default auto-detected)
    api_key: str = ""   # can be stored in config or read from env
    max_tokens: int = 4096
    temperature: float = 0.7
    max_turns: int = 50
    system_prompt: str = (
        "You are {name}, a helpful AI assistant. "
        "You have access to tools: web search, shell commands, and file operations. "
        "Be concise, helpful, and safe."
    )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentConfig":
        raw_prompt = d.get("system_prompt", "")
        name = d.get("name", "Nanobot-Lite")

        # Substitute {name} in prompt
        if raw_prompt and "{name}" in raw_prompt:
            raw_prompt = raw_prompt.replace("{name}", name)
        elif not raw_prompt:
            raw_prompt = (
                f"You are {name}, a helpful AI assistant. "
                "You have access to tools for web search, shell commands, and file operations. "
                "Be concise, helpful, and safe."
            )

        return cls(
            name=name,
            provider=d.get("provider", "opencode-zen"),
            model=d.get("model", "minimax-m2.5-free"),
            base_url=d.get("base_url", ""),
            api_key=d.get("api_key", ""),
            max_tokens=d.get("max_tokens", 4096),
            temperature=d.get("temperature", 0.7),
            max_turns=d.get("max_turns", 50),
            system_prompt=raw_prompt,
        )


@dataclass
class MemoryConfig:
    session_dir: Path = field(default_factory=lambda: Path.home() / ".nanobot_lite" / "sessions")
    max_session_messages: int = 200

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MemoryConfig":
        raw = d.get("session_dir", str(Path.home() / ".nanobot_lite" / "sessions"))
        spath = Path(raw).expanduser().resolve() if isinstance(raw, str) else Path.home() / ".nanobot_lite" / "sessions"
        return cls(
            session_dir=spath,
            max_session_messages=d.get("max_session_messages", 200),
        )


@dataclass
class LogConfig:
    level: str = "INFO"
    file: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LogConfig":
        return cls(
            level=d.get("level", "INFO"),
            file=d.get("file", ""),
        )


@dataclass
class Config:
    """Main config object. Load from YAML, save to YAML."""

    agent: AgentConfig = field(default_factory=AgentConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    log: LogConfig = field(default_factory=LogConfig)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Config":
        return cls(
            agent=AgentConfig.from_dict(d.get("agent", {})),
            telegram=TelegramConfig.from_dict(d.get("telegram", {})),
            tools=ToolsConfig.from_dict(d.get("tools", {})),
            memory=MemoryConfig.from_dict(d.get("memory", {})),
            log=LogConfig.from_dict(d.get("log", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        def _path_str(p: Any) -> Any:
            if isinstance(p, Path):
                return str(p)
            return p

        tools_limits = self.tools.limits
        return {
            "agent": {
                "name": self.agent.name,
                "provider": self.agent.provider,
                "model": self.agent.model,
                "base_url": self.agent.base_url,
                "api_key": self.agent.api_key,
                "max_tokens": self.agent.max_tokens,
                "temperature": self.agent.temperature,
                "max_turns": self.agent.max_turns,
                "system_prompt": self.agent.system_prompt,
            },
            "telegram": {
                "enabled": self.telegram.enabled,
                "bot_token": self.telegram.bot_token,
                "admin_user_id": self.telegram.admin_user_id,
                "allowed_users": self.telegram.allowed_users,
                "reply_to_incoming": self.telegram.reply_to_incoming,
            },
            "tools": {
                "workspace_dir": _path_str(self.tools.workspace_dir),
                "shell_enabled": self.tools.shell_enabled,
                "limits": {
                    "shell_timeout": tools_limits.shell_timeout,
                    "restrict_to_workspace": tools_limits.restrict_to_workspace,
                    "allowed_commands": tools_limits.allowed_commands,
                    "blocked_commands": tools_limits.blocked_commands,
                },
            },
            "memory": {
                "session_dir": _path_str(self.memory.session_dir),
                "max_session_messages": self.memory.max_session_messages,
            },
            "log": {
                "level": self.log.level,
                "file": self.log.file,
            },
        }


# ─── Config file paths & load/save ────────────────────────────────────────

DEFAULT_CONFIG_PATH = Path.home() / ".nanobot_lite" / "config.yaml"


def get_default_config_path() -> Path:
    return DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> Config:
    """Load config from YAML file."""
    cfg_path = path or DEFAULT_CONFIG_PATH
    data = _load_yaml(cfg_path)
    config = Config.from_dict(data)

    # Override with env vars
    if os.environ.get("ANTHROPIC_API_KEY"):
        pass  # handled in provider, not config

    return config


def save_config(config: Config, path: Path | None = None) -> None:
    """Save config to YAML file."""
    cfg_path = path or DEFAULT_CONFIG_PATH
    _save_yaml(config.to_dict(), cfg_path)


# ─── Default config template ───────────────────────────────────────────────

DEFAULT_CONFIG = """
telegram:
  enabled: true
  bot_token: ""
  admin_user_id: ""
  allowed_users: []
  reply_to_incoming: true

agent:
  name: "Nanobot-Lite"
  provider: "opencode-zen"  # "opencode-zen" | "anthropic"
  model: "minimax-m2.5-free"
  base_url: "https://opencode.ai/zen"
  api_key: ""  # Or set OPENCODE_API_KEY env var
  max_tokens: 4096
  temperature: 0.7
  max_turns: 50
  system_prompt: |
    You are Nanobot-Lite, a helpful AI assistant.
    You have access to tools for web search, shell commands, and file operations.
    Be concise, helpful, and safe.

memory:
  session_dir: ~/.nanobot_lite/sessions
  max_session_messages: 200

tools:
  workspace_dir: ~/nanobot_workspace
  shell_enabled: true
  limits:
    shell_timeout: 30
    restrict_to_workspace: true
    allowed_commands: []
    blocked_commands:
      - ":(){:|:&}:;"
      - "rm -rf /"
      - "rm -rf /*"
      - "dd if=/dev/zero of="
      - "mkfs"
      - "chmod -R 777 /"

log:
  level: INFO
  file: ""
"""


def ensure_default_config() -> Path:
    """Ensure default config exists, return path."""
    cfg_path = DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(DEFAULT_CONFIG.strip())
    return cfg_path