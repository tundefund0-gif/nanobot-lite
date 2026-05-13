"""Filesystem tools: read, write, edit, list."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot_lite.tools.base import Tool, ToolResult, get_registry


def _resolve_path(path: str) -> Path | None:
    """
    Resolve a path, ensuring it stays within allowed directories.
    Returns None if the path is outside the allowed area.
    """
    registry = get_registry()
    workspace = registry.get_context("workspace")
    restrict = registry.get_context("restrict_to_workspace", True)

    if workspace:
        workspace = Path(workspace).resolve()
    else:
        workspace = Path.home()

    try:
        # Resolve the target path
        target = Path(path).expanduser().resolve()

        if restrict:
            # Must be within workspace
            try:
                target.relative_to(workspace)
            except ValueError:
                # Path is outside workspace
                # Check if it's in allowed extra dirs
                extra_dirs = registry.get_context("extra_allowed_dirs", [])
                allowed = False
                for extra in extra_dirs:
                    try:
                        target.relative_to(Path(extra).resolve())
                        allowed = True
                        break
                    except ValueError:
                        continue
                if not allowed:
                    return None

        return target
    except Exception:
        return None


async def read_file(path: str, offset: int = 1, limit: int = 500) -> ToolResult:
    """Read a file, returning lines in range [offset, offset+limit]."""
    resolved = _resolve_path(path)
    if resolved is None:
        return ToolResult(content=f"Access denied: {path} is outside workspace", success=False)

    try:
        if not resolved.exists():
            return ToolResult(content=f"File not found: {path}", success=False)

        if not resolved.is_file():
            return ToolResult(content=f"Not a file: {path}", success=False)

        # Read with offset and limit
        lines = resolved.read_text(errors="replace").splitlines()

        # Adjust for 1-indexed lines
        start = max(0, offset - 1)
        end = min(len(lines), start + limit)

        selected = lines[start:end]
        total = len(lines)

        # Format output
        output_lines = []
        for i, line in enumerate(selected, start=start + 1):
            output_lines.append(f"{i:6d}|{line}")

        header = f"--- {resolved} (lines {start+1}-{end} of {total}) ---\n"
        footer = f"--- Showing {end - start} of {total} lines ---"

        return ToolResult(content=header + "\n".join(output_lines) + "\n" + footer)

    except PermissionError:
        return ToolResult(content=f"Permission denied: {path}", success=False)
    except Exception as e:
        logger.exception(f"Failed to read {path}")
        return ToolResult(content=f"Error reading {path}: {e}", success=False, error=str(e))


async def write_file(path: str, content: str) -> ToolResult:
    """Write content to a file (overwrites existing content)."""
    resolved = _resolve_path(path)
    if resolved is None:
        return ToolResult(content=f"Access denied: {path} is outside workspace", success=False)

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, errors="replace")
        return ToolResult(content=f"Written to {resolved} ({len(content)} bytes)")

    except PermissionError:
        return ToolResult(content=f"Permission denied: {path}", success=False)
    except Exception as e:
        logger.exception(f"Failed to write {path}")
        return ToolResult(content=f"Error writing {path}: {e}", success=False, error=str(e))


async def edit_file(path: str, old_string: str, new_string: str) -> ToolResult:
    """
    Replace old_string with new_string in a file.
    Uses exact string matching.
    """
    resolved = _resolve_path(path)
    if resolved is None:
        return ToolResult(content=f"Access denied: {path} is outside workspace", success=False)

    try:
        if not resolved.exists():
            return ToolResult(content=f"File not found: {path}", success=False)

        original = resolved.read_text(errors="replace")

        if old_string not in original:
            return ToolResult(content=f"Pattern not found: {repr(old_string[:50])}", success=False)

        # Replace first occurrence only
        new_content = original.replace(old_string, new_string, 1)
        resolved.write_text(new_content, errors="replace")

        return ToolResult(content=f"Edited {resolved} — replaced 1 occurrence")

    except PermissionError:
        return ToolResult(content=f"Permission denied: {path}", success=False)
    except Exception as e:
        logger.exception(f"Failed to edit {path}")
        return ToolResult(content=f"Error editing {path}: {e}", success=False, error=str(e))


async def list_dir(path: str = ".") -> ToolResult:
    """List files in a directory."""
    resolved = _resolve_path(path)
    if resolved is None:
        return ToolResult(content=f"Access denied: {path} is outside workspace", success=False)

    try:
        if not resolved.exists():
            return ToolResult(content=f"Directory not found: {path}", success=False)

        if not resolved.is_dir():
            return ToolResult(content=f"Not a directory: {path}", success=False)

        entries = []
        for entry in sorted(resolved.iterdir()):
            rel_path = entry.relative_to(resolved)
            if entry.is_dir():
                entries.append(f"{rel_path}/")
            else:
                size = entry.stat().st_size
                entries.append(f"{rel_path} ({size} bytes)")

        if not entries:
            return ToolResult(content=f"(empty directory: {resolved})")

        return ToolResult(content=f"Contents of {resolved}:\n" + "\n".join(entries))

    except PermissionError:
        return ToolResult(content=f"Permission denied: {path}", success=False)
    except Exception as e:
        logger.exception(f"Failed to list {path}")
        return ToolResult(content=f"Error listing {path}: {e}", success=False, error=str(e))


def create_filesystem_tools() -> list[Tool]:
    return [
        Tool(
            name="read_file",
            description=(
                "Read a file and return its contents with line numbers. "
                "Use offset and limit to paginate through large files. "
                "Lines are 1-indexed."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Starting line number (1-indexed, default: 1)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of lines to read (default: 500)",
                    },
                },
                "required": ["path"],
            },
            handler=read_file,
        ),
        Tool(
            name="write_file",
            description="Write content to a file, creating it if it doesn't exist. WARNING: overwrites existing content entirely.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
            handler=write_file,
        ),
        Tool(
            name="edit_file",
            description=(
                "Edit a file by replacing a specific string with new content. "
                "Uses exact string matching. Only replaces the FIRST occurrence. "
                "If the pattern appears multiple times, specify enough context to match uniquely."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact string to find and replace",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The replacement string",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
            handler=edit_file,
        ),
        Tool(
            name="list_dir",
            description="List files and directories within a directory.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the directory to list (default: '.', the workspace root)",
                    },
                },
            },
            handler=list_dir,
        ),
    ]
