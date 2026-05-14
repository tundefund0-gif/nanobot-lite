"""Shell execution tool with self-healing, command fallbacks, and path auto-discovery."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot_lite.agent.healer import get_healer, PathAutoDiscover, Strategy, Severity
from nanobot_lite.tools.base import Tool, ToolResult
from nanobot_lite.utils.helpers import run_shell_async


# ─── Safety ────────────────────────────────────────────────────────────────────

BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+\*",
    r":\(\)\{.*:\|:&\};:",   # fork bomb
    r"dd\s+if=",
    r"mkfs",
    r"mke2fs",
    r"dd.*of=/dev/",
    r">\s*/etc/",
    r"chmod\s+-R\s+777\s+/",
    r"wget.*\|.*sh",
    r"curl.*\|.*sh",
    r"python.*-m\s+pip\s+install.*--break-system-packages",
]

# Command alias / fallback chains
# Maps a primary command → list of alternatives (tried in order)
COMMAND_ALTERNATIVES: dict[str, list[str]] = {
    "grep":   ["rg", "fgrep", "egrep"],
    "rg":     ["grep", "egrep", "fgrep"],
    "find":   ["find", "fd", "python"],
    "curl":   ["curl", "wget", "python"],
    "wget":   ["wget", "curl", "python"],
    "jq":     ["jq", "python", "ruby", "node"],
    "sed":    ["sed", "awk", "python", "perl"],
    "awk":    ["awk", "gawk", "python"],
    "make":   ["make", "cmake", "ninja", "python"],
    "gcc":    ["gcc", "clang", "cc"],
    "clang":  ["clang", "gcc", "cc"],
    "tar":    ["tar", "python", "zip", "unzip"],
    "zip":    ["zip", "python", "7z"],
    "unzip":  ["unzip", "python", "7z"],
    "git":    ["git"],
    "pip":    ["pip", "pip3", "python -m pip", "python3 -m pip"],
    "python": ["python3", "python", "python3.11", "python3.10", "py"],
    "python3":["python", "python3", "python3.11", "python3.10", "py"],
    "node":   ["node", "nodejs"],
    "ruby":   ["ruby", "ruby3", "ruby3.0"],
    "php":    ["php", "php8", "php7"],
    "java":   ["java", "java17", "openjdk"],
    "ffmpeg": ["ffmpeg", "avconv"],
    "convert": ["convert", "ffmpeg", "python"],
    "convert": ["convert", "magick"],
}


def is_blocked(command: str) -> tuple[bool, str]:
    """Check if a command contains blocked patterns."""
    cmd_lower = command.lower()
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd_lower, re.IGNORECASE):
            return True, f"Command matches blocked pattern: {pattern}"
    return False, ""


def _which(cmd: str) -> str | None:
    """Return path to command if available, else None."""
    path = shutil.which(cmd)
    if path:
        return path
    # Try common locations
    for location in ["/usr/bin", "/usr/local/bin", "/bin", "/sbin", "/opt/bin"]:
        candidate = Path(location) / cmd
        if candidate.exists():
            return str(candidate)
    return None


def _extract_primary_cmd(command: str) -> str:
    """Extract the primary command name from a shell command."""
    stripped = command.strip()
    # Handle pipes, redirects, &&
    for sep in (" | ", " && ", " || ", " ; ", " > ", " >> ", " 2>", " < "):
        if sep in stripped:
            stripped = stripped.split(sep)[0].strip()
    parts = stripped.split()
    return parts[0] if parts else stripped


# ─── Main tool ────────────────────────────────────────────────────────────────

async def exec_shell(
    command: str,
    cwd: str | None = None,
    timeout: int = 30,
) -> ToolResult:
    """
    Execute a shell command with deep self-healing:

    - Safety: blocked pattern detection
    - Command fallbacks: tries alternatives if command not found
    - Path recovery: suggests similar paths on FileNotFoundError
    - Multi-pass: retries with fallback commands if command not found
    """
    registry = get_registry_cached()
    workspace = registry.get_context("workspace") if registry else None
    shell_enabled = registry.get_context("shell_enabled", True) if registry else True
    restrict_to_workspace = registry.get_context("restrict_to_workspace", True) if registry else True
    allowed_commands = registry.get_context("allowed_commands", []) if registry else []
    blocked_commands = registry.get_context("blocked_commands", []) if registry else []

    if not shell_enabled:
        return ToolResult(content="Shell execution is disabled.", success=False)

    # Safety check
    blocked, reason = is_blocked(command)
    if blocked:
        logger.warning(f"Blocked command: {reason}")
        return ToolResult(content=f"Command blocked for safety: {reason}", success=False)

    # Allowed/blocked command lists
    if allowed_commands:
        cmd_name = _extract_primary_cmd(command)
        if cmd_name not in allowed_commands:
            return ToolResult(
                content=f"Command '{cmd_name}' not in allowed list: {', '.join(allowed_commands)}",
                success=False,
            )

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

    healer = get_healer()

    # ── Try with command fallbacks ─────────────────────────────────────────────
    primary_cmd = _extract_primary_cmd(command)
    fallback_chain = [primary_cmd] + COMMAND_ALTERNATIVES.get(primary_cmd, [])

    # Deduplicate while preserving order
    seen = set()
    unique_chain = []
    for c in fallback_chain:
        if c not in seen and _which(c) is not None:
            seen.add(c)
            unique_chain.append(c)

    # Try each command in chain
    results_log: list[dict] = []

    for cmd_to_try in unique_chain:
        cmd_path = _which(cmd_to_try)
        if cmd_path is None:
            continue

        # Substitute this command into the full command
        cmd_variant = command.replace(primary_cmd, cmd_to_try, 1)
        if cmd_variant == command:
            # Also try without substitution for piped commands
            cmd_variant = command

        try:
            exit_code, stdout, stderr = await run_shell_async(
                cmd_variant, timeout=timeout, cwd=cwd,
            )

            if exit_code == 0:
                healer.record_tool_result("shell", success=True)
                output = stdout if stdout else "(no output)"
                return ToolResult(content=output, success=True)

            error_output = stderr if stderr else stdout if stdout else ""
            error_output += f"\n[exit code: {exit_code}]"

            # Non-recoverable? Don't try alternatives
            if "Permission denied" in error_output and cmd_to_try == primary_cmd:
                healer.record_tool_result("shell", success=False, fatal=True)
                return ToolResult(content=error_output, success=False, error=error_output)

            results_log.append({
                "cmd": cmd_to_try,
                "exit_code": exit_code,
                "stderr": stderr,
                "stdout": stdout,
            })

        except asyncio.TimeoutError:
            healer.record_tool_result("shell", success=False, heal_passes=1)
            plan = healer.diagnose(f"TimeoutExpired after {timeout}s")
            return ToolResult(
                content=f"⏱️ Command timed out after {timeout}s\n\n"
                        f"🔧 *Diagnosis:* {plan.diagnosis}\n"
                        f"📋 Increase timeout or simplify the command.",
                success=False,
                error=f"Timeout: {cmd_variant}",
            )

    # All fallbacks failed
    last_result = results_log[-1] if results_log else {}
    error_text = last_result.get("stderr", "") or last_result.get("stdout", "")
    exit_code = last_result.get("exit_code", 1)

    plan = healer.diagnose(error_text)

    # Try path recovery for FileNotFoundError
    if "No such file" in error_text or "not found" in error_text.lower():
        path_match = re.search(r"['\"]([^'\"]+)['\"]", error_text)
        if path_match:
            guessed = path_match.group(1)
            similar = PathAutoDiscover.find_similar(guessed, str(workspace) if workspace else None)
            if similar:
                plan.fix_hints.append(f"Similar files found: {', '.join(similar[:3])}")

    healer.record_tool_result("shell", success=False)

    output = error_text if error_text else f"Exit code: {exit_code}"
    hint_lines = "\n".join(f"  • {h}" for h in plan.fix_hints)

    return ToolResult(
        content=(
            f"❌ Shell: exit `{exit_code}`\n"
            f"🔧 *Diagnosis:* {plan.diagnosis}\n"
            f"📋 *Fix hints:*\n{hint_lines}\n\n"
            f"```\n{output[:2000]}\n```"
        ),
        success=False,
        error=error_text,
    )


def get_registry_cached():
    """Lazy import to avoid circular deps."""
    try:
        from nanobot_lite.tools.base import get_registry
        return get_registry()
    except Exception:
        return None


# ─── Tool factory ─────────────────────────────────────────────────────────────

def create_shell_tool() -> Tool:
    return Tool(
        name="shell",
        description=(
            "Execute a shell command on the system. "
            "Use for running programs, file operations, git commands, etc. "
            "Returns stdout, stderr, and exit code. "
            "Supports command fallbacks (e.g., grep→rg→find) and path auto-discovery. "
            "DANGEROUS COMMANDS ARE BLOCKED."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "cwd":     {"type": "string", "description": "Working directory (optional)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
            },
            "required": ["command"],
        },
        handler=exec_shell,
    )
