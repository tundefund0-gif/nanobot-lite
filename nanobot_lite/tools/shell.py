"""Shell execution tool."""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot_lite.tools.base import Tool, ToolResult, get_registry
from nanobot_lite.utils.helpers import run_shell_async


BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+\*",
    r":\(\)\{.*:\|:&\};:",  # fork bomb
    r"dd\s+if=",
    r"mkfs",
    r"mke2fs",
    r"dd.*of=/dev/",
    r">\s*/etc/",
    r"chmod\s+-R\s+777\s+/",
]


def is_blocked(command: str) -> tuple[bool, str]:
    """Check if a command contains blocked patterns."""
    cmd_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd_lower, re.IGNORECASE):
            return True, f"Command matches blocked pattern: {pattern}"
    return False, ""


async def exec_shell(command: str, cwd: str | None = None, timeout: int = 30) -> ToolResult:
    """
    Execute a shell command.

    Args:
        command: The shell command to execute
        cwd: Working directory (default: workspace)
        timeout: Timeout in seconds
    """
    registry = get_registry()
    workspace = registry.get_context("workspace")
    shell_enabled = registry.get_context("shell_enabled", True)
    restrict_to_workspace = registry.get_context("restrict_to_workspace", True)
    allowed_commands = registry.get_context("allowed_commands", [])
    blocked_commands = registry.get_context("blocked_commands", [])

    if not shell_enabled:
        return ToolResult(content="Shell execution is disabled.", success=False)

    # Check blocked patterns
    blocked, reason = is_blocked(command)
    if blocked:
        logger.warning(f"Blocked command: {reason}")
        return ToolResult(content=f"Command blocked for safety: {reason}", success=False)

    # Check allowed commands list
    if allowed_commands:
        cmd_name = command.strip().split()[0] if command.strip() else ""
        if cmd_name not in allowed_commands:
            return ToolResult(
                content=f"Command '{cmd_name}' not in allowed list: {', '.join(allowed_commands)}",
                success=False,
            )

    # Check blocked commands list
    cmd_lower = command.lower()
    for blocked in blocked_commands:
        if blocked.lower() in cmd_lower:
            return ToolResult(
                content=f"Command contains blocked pattern: {blocked}",
                success=False,
            )

    # Resolve working directory
    if cwd is None:
        cwd = str(workspace) if workspace else os.getcwd()

    # Restrict to workspace
    if restrict_to_workspace and workspace:
        try:
            cwd_path = Path(cwd).resolve()
            if not str(cwd_path).startswith(str(Path(workspace).resolve())):
                return ToolResult(
                    content=f"Working directory must be within workspace: {workspace}",
                    success=False,
                )
        except Exception:
            pass

    try:
        exit_code, stdout, stderr = await run_shell_async(command, timeout=timeout, cwd=cwd)

        output = ""
        if stdout:
            output += f"STDOUT:\n{stdout}"
        if stderr:
            output += f"\nSTDERR:\n{stderr}"
        if not output:
            output = "(no output)"

        output += f"\n[exit code: {exit_code}]"
        return ToolResult(content=output, success=exit_code == 0)

    except Exception as e:
        logger.exception("Shell execution failed")
        return ToolResult(content=f"Shell execution failed: {e}", success=False, error=str(e))


def create_shell_tool() -> Tool:
    return Tool(
        name="shell",
        description=(
            "Execute a shell command on the system. "
            "Use for running programs, file operations, git commands, etc. "
            "Returns stdout, stderr, and exit code. "
            "DANGEROUS COMMANDS ARE BLOCKED."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory (optional, defaults to workspace)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30)",
                },
            },
            "required": ["command"],
        },
        handler=exec_shell,
    )
