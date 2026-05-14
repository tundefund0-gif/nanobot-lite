"""Loads .env files and merges into os.environ."""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Generator

try:
    from loguru import logger
except ImportError:
    import sys as _sys
    class _Dummy:
        def debug(self, *a, **k): pass
        def info(self, *a, **k): pass
        def warning(self, *a, **k): print(*a, file=_sys.stderr)
        def error(self, *a, **k): print(*a, file=_sys.stderr)
        def success(self, *a, **k): pass
    logger = _Dummy()


# ─── Parsing ──────────────────────────────────────────────────────────────────

def _parse_env_line(line: str) -> Generator[tuple[str, str], None, None]:
    """Yield (key, value) from a single .env line, or nothing if comment/empty."""
    line = line.strip()

    # Skip blank lines and comments
    if not line or line.startswith("#"):
        return

    # Strip leading `export ` (bash convention)
    if line.startswith("export "):
        line = line[7:]

    # Find the first = sign
    if "=" not in line:
        return

    key, raw_value = line.split("=", 1)
    key = key.strip()
    if not key or not _is_valid_key(key):
        return

    value = raw_value.strip()

    # Strip quotes
    if len(value) >= 2:
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

    yield key, value


def _is_valid_key(key: str) -> bool:
    """Keys must be alphanumeric + underscore, no leading digit."""
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key))


# ─── Core functions ────────────────────────────────────────────────────────────

def load_env(path: str = "~/.nanobot_lite/.env") -> dict[str, str]:
    """
    Load all key=value pairs from a .env file.
    Returns dict of parsed variables (does NOT modify os.environ).
    """
    env = {}
    path = os.path.expanduser(path)

    if not os.path.exists(path):
        logger.debug(f".env file not found at {path}")
        return env

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                for key, value in _parse_env_line(line):
                    env[key] = value
    except OSError as e:
        logger.warning(f"Failed to read .env: {e}")

    logger.debug(f"Loaded {len(env)} vars from {path}")
    return env


def expand_vars(value: str, env: dict[str, str]) -> str:
    """
    Expand ${VAR} references in a value.
    e.g. "TOKEN=${OPENCODE_API_KEY}-suffix" → "TOKEN=abc-suffix"
    """
    for key, val in env.items():
        value = value.replace(f"${{{key}}}", val)
    return value


def merge_with_os(env: dict[str, str], precedence: str = "os") -> None:
    """
    Merge .env values into os.environ.

    Args:
        env: dict of loaded variables
        precedence: "env" means .env overwrites OS vars,
                   "os" means OS vars take precedence (default)
    """
    for key, value in env.items():
        if precedence == "env" or key not in os.environ:
            os.environ[key] = value


def load_and_merge(
    env_path: str = "~/.nanobot_lite/.env",
    precedence: str = "os",
) -> dict[str, str]:
    """
    One-call load + expand + merge.
    Returns the loaded dict.
    """
    env = load_env(env_path)
    env = {k: expand_vars(v, env) for k, v in env.items()}
    merge_with_os(env, precedence)
    return env


# ─── CLI helpers ──────────────────────────────────────────────────────────────

def env_set(key: str, value: str, env_path: str = "~/.nanobot_lite/.env") -> bool:
    """Set a key in the .env file (create if missing)."""
    env_path = os.path.expanduser(env_path)
    env = load_env(env_path)
    env[key] = value

    os.makedirs(os.path.dirname(env_path), exist_ok=True)
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            for k, v in env.items():
                if " " in v or '"' in v or "'" in v or "\n" in v:
                    f.write(f'{k}="{v}"\n')
                else:
                    f.write(f"{k}={v}\n")
        logger.success(f"Set {key} in {env_path}")
        return True
    except OSError as e:
        logger.error(f"Failed to write .env: {e}")
        return False


def env_unset(key: str, env_path: str = "~/.nanobot_lite/.env") -> bool:
    """Remove a key from the .env file."""
    env_path = os.path.expanduser(env_path)
    env = load_env(env_path)

    if key not in env:
        logger.warning(f"Key '{key}' not found in .env")
        return False

    del env[key]
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            for k, v in env.items():
                f.write(f"{k}={v}\n")
        logger.success(f"Removed {key} from {env_path}")
        return True
    except OSError as e:
        logger.error(f"Failed to write .env: {e}")
        return False


def env_list(env_path: str = "~/.nanobot_lite/.env") -> list[tuple[str, str]]:
    """Return sorted list of (key, value) pairs, with sensitive values redacted."""
    env = load_env(env_path)
    sensitive = {"api_key", "token", "password", "secret", "key", "auth"}
    result = []
    for k, v in sorted(env.items()):
        is_sensitive = any(s in k.lower() for s in sensitive)
        display = "***" if is_sensitive else v
        result.append((k, display))
    return result


# ─── Auto-load on import ───────────────────────────────────────────────────────

# Automatically load ~/.nanobot_lite/.env on module import (safe — OS vars win)
_default_env = load_and_merge()