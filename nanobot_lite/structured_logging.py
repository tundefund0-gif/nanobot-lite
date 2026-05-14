"""Configurable structured logging with JSON output and rotating files."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

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


# ─── Constants ────────────────────────────────────────────────────────────────

MAX_LOG_SIZE = 5 * 1024 * 1024   # 5 MB
MAX_LOG_FILES = 5
LOG_DIR = os.path.expanduser("~/.nanobot_lite/logs")
APP_LOG = os.path.join(LOG_DIR, "app.log")
PLUGIN_LOG_DIR = os.path.join(LOG_DIR, "plugins")


# ─── Log Level ────────────────────────────────────────────────────────────────

class LogLevel:
    DEBUG = "DEBUG"
    INFO  = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


# ─── Colour codes ──────────────────────────────────────────────────────────────

COLOURS = {
    "DEBUG":   "\033[36m",   # cyan
    "INFO":    "\033[34m",   # blue
    "WARNING": "\033[33m",   # yellow
    "ERROR":   "\033[31m",   # red
    "SUCCESS": "\033[32m",   # green
    "RESET":   "\033[0m",
}


# ─── Log Entry ────────────────────────────────────────────────────────────────

@dataclass
class LogEntry:
    timestamp: str
    level: str
    event: str
    module: str
    message: str
    user_id: str | None = None
    duration_ms: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "timestamp": self.timestamp,
            "level": self.level,
            "event": self.event,
            "module": self.module,
            "message": self.message,
        }
        if self.user_id:
            d["user_id"] = self.user_id
        if self.duration_ms is not None:
            d["duration_ms"] = self.duration_ms
        if self.extra:
            d["extra"] = self.extra
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ─── Rotating File Handler ─────────────────────────────────────────────────────

class RotatingFileHandler:
    """Thread-safe rotating log file handler (no stdlib logging dependency)."""

    def __init__(self, path: str, max_bytes: int = MAX_LOG_SIZE, backup_count: int = MAX_LOG_FILES):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, line: str) -> None:
        with self._lock:
            # Rotate if needed
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                self._rotate()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def _rotate(self) -> None:
        # Remove oldest backup
        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        if oldest.exists():
            oldest.unlink()
        # Shift others
        for i in range(self.backup_count - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{i}")
            dst = self.path.with_name(f"{self.path.name}.{i + 1}")
            if src.exists():
                src.rename(dst)
        # Current becomes .1
        self.path.rename(self.path.with_name(f"{self.path.name}.1"))
        # Create fresh file
        self.path.touch()


# ─── Structured Logger ─────────────────────────────────────────────────────────

class StructuredLogger:
    """
    Production-ready structured logger with JSON output option.

    Usage:
        log = StructuredLogger("agent")
        log.info("llm_call", model="claude-3", tokens=1500)
        log.log_healing("shell", "FileNotFoundError", pass=2, success=True)
    """

    def __init__(
        self,
        name: str,
        config_dir: str = "~/.nanobot_lite",
        json_mode: bool = False,
    ):
        self.name = name
        self.json_mode = json_mode
        self._lock = Lock()

        config_dir = os.path.expanduser(config_dir)
        log_dir = os.path.join(config_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)

        self.file_handler = RotatingFileHandler(
            os.path.join(log_dir, f"{name}.log"),
            MAX_LOG_SIZE,
            MAX_LOG_FILES,
        )

    # ── Core logging ───────────────────────────────────────────────────────────

    def _format(self, level: str, event: str, message: str, **kwargs) -> str:
        ts = datetime.now(timezone.utc).isoformat(timespec="millis")
        entry = LogEntry(
            timestamp=ts,
            level=level,
            event=event,
            module=self.name,
            message=message,
            **kwargs,
        )

        if self.json_mode:
            return entry.to_json()

        colour = COLOURS.get(level, "")
        reset  = COLOURS["RESET"]
        extras = " ".join(f"{k}={v}" for k, v in kwargs.items() if k not in ("user_id", "duration_ms"))
        return (
            f"{colour}[{ts}][{level}][{self.name}]{reset} "
            f"{event}: {message}"
            + (f" | {extras}" if extras else "")
        )

    def _write(self, line: str) -> None:
        print(line, flush=True)
        self.file_handler.write(line)

    def _log(self, level: str, event: str, message: str = "", **kwargs) -> None:
        line = self._format(level, event, message, **kwargs)
        with self._lock:
            self._write(line)

    # ── Public API ─────────────────────────────────────────────────────────────

    def debug(self, event: str, message: str = "", **kwargs) -> None:
        self._log("DEBUG", event, message, **kwargs)

    def info(self, event: str, message: str = "", **kwargs) -> None:
        self._log("INFO", event, message, **kwargs)

    def warning(self, event: str, message: str = "", **kwargs) -> None:
        self._log("WARNING", event, message, **kwargs)

    def error(self, event: str, message: str = "", **kwargs) -> None:
        self._log("ERROR", event, message, **kwargs)

    def success(self, event: str, message: str = "", **kwargs) -> None:
        self._log("SUCCESS", event, message, **kwargs)

    # ── Structured events ──────────────────────────────────────────────────────

    def log_tool_call(
        self,
        tool: str,
        duration_ms: float,
        success: bool,
        user_id: str | None = None,
        error: str | None = None,
        **kwargs,
    ) -> None:
        level = "SUCCESS" if success else "ERROR"
        self._log(
            level,
            "tool_call",
            f"{tool} {'✓' if success else '✗'} ({duration_ms:.0f}ms)",
            duration_ms=duration_ms,
            user_id=user_id,
            tool=tool,
            success=success,
            error=error,
            **kwargs,
        )

    def log_llm_call(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
        duration_ms: float,
        user_id: str | None = None,
        **kwargs,
    ) -> None:
        self._log(
            "INFO",
            "llm_call",
            f"{model} in={tokens_in} out={tokens_out} ({duration_ms:.0f}ms)",
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
            user_id=user_id,
            **kwargs,
        )

    def log_healing(
        self,
        tool: str,
        error: str,
        pass_number: int,
        success: bool,
        fix: str | None = None,
        user_id: str | None = None,
    ) -> None:
        level = "SUCCESS" if success else "WARNING"
        msg = f"{tool} healing pass {pass_number}: {error}" + (f" → {fix}" if fix else "")
        self._log(
            level,
            "healing",
            msg,
            tool=tool,
            error=error,
            healing_pass=pass_number,
            success=success,
            fix=fix,
            user_id=user_id,
        )

    def log_session(
        self,
        user_id: str,
        message_count: int,
        tools_used: int,
        duration_s: float,
    ) -> None:
        self._log(
            "INFO",
            "session_end",
            f"user={user_id} msgs={message_count} tools={tools_used} {duration_s:.1f}s",
            user_id=user_id,
            message_count=message_count,
            tools_used=tools_used,
            duration_s=duration_s,
        )

    def log_startup(self, version: str, platform: str, python_version: str) -> None:
        self._log(
            "SUCCESS",
            "startup",
            f"Nanobot-Lite {version} on {platform} (Python {python_version})",
            version=version,
            platform=platform,
            python_version=python_version,
        )

    def log_shutdown(self, uptime_s: float, total_turns: int, total_tools: int) -> None:
        self._log(
            "INFO",
            "shutdown",
            f"Uptime {uptime_s:.0f}s | turns={total_turns} tools={total_tools}",
            uptime_s=uptime_s,
            total_turns=total_turns,
            total_tools=total_tools,
        )


# ─── Global app logger (for use across the codebase) ───────────────────────────

_app_logger: StructuredLogger | None = None

def get_app_logger(name: str = "nanobot", json_mode: bool = False) -> StructuredLogger:
    global _app_logger
    if _app_logger is None:
        _app_logger = StructuredLogger(name, json_mode=json_mode)
    return _app_logger


# ─── Plugin-specific logger factory ────────────────────────────────────────────

def get_plugin_logger(plugin_name: str) -> StructuredLogger:
    log_dir = os.path.expanduser(PLUGIN_LOG_DIR)
    os.makedirs(log_dir, exist_ok=True)
    return StructuredLogger(f"plugin:{plugin_name}")