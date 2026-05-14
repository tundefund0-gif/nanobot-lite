"""Filesystem tools with self-healing: path auto-discovery, backup, rollback."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot_lite.agent.healer import get_healer, PathAutoDiscover
from nanobot_lite.tools.base import Tool, ToolResult


def _resolve_path(path: str, allow_create: bool = False) -> Path | None:
    """Resolve a path, ensuring it stays within allowed directories."""
    try:
        from nanobot_lite.tools.base import get_registry
        registry = get_registry()
        workspace = registry.get_context("workspace")
        restrict = registry.get_context("restrict_to_workspace", True)
    except Exception:
        workspace = None
        restrict = True

    if workspace:
        workspace = Path(workspace).resolve()
    else:
        workspace = Path.home()

    try:
        target = Path(path).expanduser().resolve()

        if restrict:
            try:
                target.relative_to(workspace)
            except ValueError:
                extra_dirs = []
                try:
                    from nanobot_lite.tools.base import get_registry
                    registry = get_registry()
                    extra_dirs = registry.get_context("extra_allowed_dirs", [])
                except Exception:
                    pass

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
    """Read a file with self-healing suggestions on failure."""
    resolved = _resolve_path(path)
    if resolved is None:
        healer = get_healer()
        similar = PathAutoDiscover.find_similar(path)
        hint = f"\n\n📁 *Did you mean:* {', '.join(similar[:3])}" if similar else ""
        return ToolResult(
            content=f"Access denied: {path} is outside workspace{hint}",
            success=False,
        )

    try:
        if not resolved.exists():
            healer = get_healer()
            similar = PathAutoDiscover.find_similar(path)
            hint = ""
            if similar:
                hint = f"\n\n📁 *Similar files found:*\n" + "\n".join(f"  • {s}" for s in similar[:5])
            return ToolResult(
                content=f"File not found: {path}{hint}",
                success=False,
            )

        if not resolved.is_file():
            return ToolResult(content=f"Not a file: {path}", success=False)

        lines = resolved.read_text(errors="replace").splitlines()
        start = max(0, offset - 1)
        end = min(len(lines), start + limit)
        selected = lines[start:end]
        total = len(lines)

        output_lines = [f"{i:6d}|{line}" for i, line in enumerate(selected, start=start + 1)]
        header = f"--- {resolved} (lines {start+1}-{end} of {total}) ---\n"
        footer = f"--- Showing {end - start} of {total} lines ---"

        get_healer().record_tool_result("read_file", success=True)
        return ToolResult(content=header + "\n".join(output_lines) + "\n" + footer)

    except PermissionError:
        get_healer().record_tool_result("read_file", success=False)
        return ToolResult(content=f"Permission denied: {path}", success=False)
    except UnicodeDecodeError:
        # Binary file — try binary read
        try:
            data = resolved.read_bytes()
            size = len(data)
            snippet = data[:500].hex()
            return ToolResult(
                content=f"Binary file ({size} bytes), first 500 bytes as hex:\n{snippet}",
                success=True,
            )
        except Exception as e:
            return ToolResult(content=f"Error reading {path}: {e}", success=False, error=str(e))
    except Exception as e:
        logger.exception(f"Failed to read {path}")
        get_healer().record_tool_result("read_file", success=False)
        return ToolResult(content=f"Error reading {path}: {e}", success=False, error=str(e))


async def write_file(path: str, content: str, backup: bool = True) -> ToolResult:
    """Write content to a file with automatic backup before overwrite."""
    resolved = _resolve_path(path, allow_create=True)
    if resolved is None:
        return ToolResult(content=f"Access denied: {path} is outside workspace", success=False)

    healer = get_healer()

    # Backup existing file
    if backup and resolved.exists():
        bk = healer.rollback.backup(resolved)
        if bk:
            logger.info(f"Backed up {resolved} → {bk}")

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, errors="replace")
        healer.record_tool_result("write_file", success=True)
        return ToolResult(content=f"Written to {resolved} ({len(content)} bytes)")

    except PermissionError:
        healer.record_tool_result("write_file", success=False)
        return ToolResult(content=f"Permission denied: {path}", success=False)
    except OSError as e:
        if e.errno == 28:  # ENOSPC
            healer.record_tool_result("write_file", success=False, fatal=True)
            return ToolResult(content="No space left on device.", success=False, error=str(e))
        logger.exception(f"Failed to write {path}")
        healer.record_tool_result("write_file", success=False)
        return ToolResult(content=f"Error writing {path}: {e}", success=False, error=str(e))


async def edit_file(path: str, old_string: str, new_string: str,
                   use_regex: bool = False, replace_all: bool = False) -> ToolResult:
    """
    Replace text in a file with self-healing:

    - Auto-backup before edit
    - Rollback on failure
    - Regex support
    - Diff preview
    - Context-aware suggestions on pattern not found
    """
    resolved = _resolve_path(path)
    if resolved is None:
        return ToolResult(content=f"Access denied: {path} is outside workspace", success=False)

    healer = get_healer()

    if not resolved.exists():
        return ToolResult(content=f"File not found: {path}", success=False)

    try:
        original = resolved.read_text(errors="replace")

        # Backup before edit
        bk_path = healer.rollback.backup(resolved)

        # Try to match
        if use_regex:
            if replace_all:
                new_content, count = re.subn(old_string, new_string, original, count=0)
            else:
                new_content, count = re.subn(old_string, new_string, original, count=1)
            if count == 0:
                healer.rollback.restore(resolved, bk_path)
                # Try to suggest similar patterns
                matches = re.findall(old_string, original)
                hint = ""
                if matches:
                    hint = f"\n\n💡 Found {len(matches)} similar match(es). Check your regex pattern."
                return ToolResult(
                    content=f"Pattern not found (regex): {repr(old_string[:50])}{hint}",
                    success=False,
                )
            action_desc = f"replaced {count} occurrence(s)"
        else:
            if old_string not in original:
                healer.rollback.restore(resolved, bk_path)
                # Suggest close matches
                close = []
                for line in original.splitlines():
                    if old_string[:20].lower() in line.lower():
                        close.append(line.strip()[:80])
                hint = ""
                if close:
                    hint = "\n\n💡 *Similar lines found:*\n" + "\n".join(f"  • {l}" for l in close[:3])
                return ToolResult(
                    content=f"Pattern not found: {repr(old_string[:50])}{hint}",
                    success=False,
                )
            if replace_all:
                new_content = original.replace(old_string, new_string)
                count = original.count(old_string)
            else:
                new_content = original.replace(old_string, new_string, 1)
                count = 1
            action_desc = f"replaced {count} occurrence(s)"

        resolved.write_text(new_content, errors="replace")
        healer.record_tool_result("edit_file", success=True)

        # Show diff summary
        diff_lines = []
        for i, (old_line, new_line) in enumerate(zip(original.splitlines(), new_content.splitlines())):
            if old_line != new_line:
                diff_lines.append(f"  {i+1}: {old_line[:60]!r} → {new_line[:60]!r}")
        diff_summary = ""
        if diff_lines:
            diff_summary = "\n\n📝 *Changes:*\n" + "\n".join(diff_lines[:10])

        return ToolResult(
            content=f"✅ Edited {resolved} — {action_desc}. Backup at: {bk_path}{diff_summary}"
        )

    except PermissionError:
        healer.record_tool_result("edit_file", success=False)
        return ToolResult(content=f"Permission denied: {path}", success=False)
    except Exception as e:
        logger.exception(f"Failed to edit {path}")
        healer.rollback.restore(resolved)
        healer.record_tool_result("edit_file", success=False)
        return ToolResult(content=f"Error editing {path}: {e}", success=False, error=str(e))


async def list_dir(path: str = ".") -> ToolResult:
    """List files with size, type, and quick stats."""
    resolved = _resolve_path(path)
    if resolved is None:
        return ToolResult(content=f"Access denied: {path} is outside workspace", success=False)

    try:
        if not resolved.exists():
            similar = PathAutoDiscover.find_similar(path)
            hint = f"\n\n📁 *Similar:* {', '.join(similar[:3])}" if similar else ""
            return ToolResult(content=f"Directory not found: {path}{hint}", success=False)

        if not resolved.is_dir():
            return ToolResult(content=f"Not a directory: {path}", success=False)

        entries = []
        total_size = 0
        dir_count = 0
        file_count = 0

        for entry in sorted(resolved.iterdir()):
            try:
                if entry.is_dir():
                    sub_count = len(list(entry.iterdir()))
                    entries.append(f"{entry.name}/  ({sub_count} items)")
                    dir_count += 1
                else:
                    size = entry.stat().st_size
                    total_size += size
                    if size > 1024 * 1024:
                        size_str = f"{size / (1024*1024):.1f}MB"
                    elif size > 1024:
                        size_str = f"{size / 1024:.1f}KB"
                    else:
                        size_str = f"{size}B"
                    entries.append(f"{entry.name}  ({size_str})")
                    file_count += 1
            except PermissionError:
                entries.append(f"{entry.name}  (permission denied)")

        header = (
            f"📁 *{resolved}*\n"
            f"{file_count} file(s), {dir_count} dir(s), "
            f"total {total_size / (1024*1024):.2f}MB\n"
        )

        if not entries:
            return ToolResult(content=header + "(empty)")

        get_healer().record_tool_result("list_dir", success=True)
        return ToolResult(content=header + "\n".join(entries))

    except PermissionError:
        get_healer().record_tool_result("list_dir", success=False)
        return ToolResult(content=f"Permission denied: {path}", success=False)
    except Exception as e:
        logger.exception(f"Failed to list {path}")
        get_healer().record_tool_result("list_dir", success=False)
        return ToolResult(content=f"Error listing {path}: {e}", success=False, error=str(e))


async def rollback_file(path: str, backup_index: int = -1) -> ToolResult:
    """Restore a file from its most recent backup."""
    resolved = _resolve_path(path)
    if resolved is None:
        return ToolResult(content=f"Access denied: {path} is outside workspace", success=False)

    healer = get_healer()
    backups = healer.rollback.get_backups(resolved)

    if not backups:
        return ToolResult(content=f"No backups found for {path}", success=False)

    target_backup = backups[backup_index] if abs(backup_index) < len(backups) else backups[-1]
    ok = healer.rollback.restore(resolved, target_backup)

    if ok:
        healer.record_tool_result("rollback_file", success=True)
        return ToolResult(content=f"✅ Restored {path} from {target_backup.name}")
    else:
        return ToolResult(content=f"❌ Rollback failed for {path}", success=False)


def create_filesystem_tools() -> list[Tool]:
    return [
        Tool(
            name="read_file",
            description=(
                "Read a file and return its contents with line numbers. "
                "Lines are 1-indexed. Supports path auto-discovery if file not found."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path":   {"type": "string", "description": "Path to the file to read"},
                    "offset": {"type": "integer", "description": "Starting line number (1-indexed, default: 1)"},
                    "limit":  {"type": "integer", "description": "Max lines to read (default: 500)"},
                },
                "required": ["path"],
            },
            handler=read_file,
        ),
        Tool(
            name="write_file",
            description=(
                "Write content to a file (overwrites). Auto-backs up existing files. "
                "Creates parent directories automatically."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Path to the file to write"},
                    "content": {"type": "string", "description": "The content to write"},
                    "backup":  {"type": "boolean", "description": "Auto-backup before write (default: True)"},
                },
                "required": ["path", "content"],
            },
            handler=write_file,
        ),
        Tool(
            name="edit_file",
            description=(
                "Edit a file by replacing text. Supports exact match and regex. "
                "Backs up before edit. Rolls back on failure. "
                "Suggests similar lines when pattern not found."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path":        {"type": "string", "description": "Path to the file to edit"},
                    "old_string":  {"type": "string", "description": "The exact string to find (or regex if use_regex=True)"},
                    "new_string":  {"type": "string", "description": "The replacement string"},
                    "use_regex":   {"type": "boolean", "description": "Treat old_string as regex (default: False)"},
                    "replace_all": {"type": "boolean", "description": "Replace all occurrences (default: False)"},
                },
                "required": ["path", "old_string", "new_string"],
            },
            handler=edit_file,
        ),
        Tool(
            name="list_dir",
            description="List files and directories with sizes and item counts.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to directory (default: '.', workspace root)"},
                },
            },
            handler=list_dir,
        ),
        Tool(
            name="rollback_file",
            description="Restore a file from its most recent automatic backup.",
            input_schema={
                "type": "object",
                "properties": {
                    "path":         {"type": "string", "description": "Path to the file to restore"},
                    "backup_index": {"type": "integer", "description": "Backup index: -1 = most recent (default), -2 = previous, etc."},
                },
                "required": ["path"],
            },
            handler=rollback_file,
        ),
    ]
