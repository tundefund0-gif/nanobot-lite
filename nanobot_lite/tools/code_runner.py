"""Multi-language code runner for Nanobot-Lite."""
from __future__ import annotations

__version__ = "0.3.0"

import asyncio
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot_lite.tools.base import Tool, ToolResult

# ─── Language detection ─────────────────────────────────────────────────────

LANG_MAP = {
    "python": ("py", "python3"),
    "python3": ("py", "python3"),
    "py": ("py", "python3"),
    "javascript": ("js", "node"),
    "node": ("js", "node"),
    "js": ("js", "node"),
    "ruby": ("rb", "ruby"),
    "php": ("php", "php"),
    "bash": ("sh", "/bin/bash"),
    "sh": ("sh", "/bin/bash"),
    "shell": ("sh", "/bin/bash"),
    "lua": ("lua", "lua5.3"),
    "c": ("c", "gcc"),
}


def detect_language(code: str) -> str:
    """Auto-detect language from code content."""
    code = code.strip()

    if code.startswith("#!/"):
        shebang = code.split("\n")[0]
        if "python" in shebang:
            return "py"
        if "node" in shebang or "bash" in shebang:
            return "sh"
        if "ruby" in shebang:
            return "rb"
        if "php" in shebang:
            return "php"

    # Shebang on first line
    if "\n#!" in code:
        first = code[: code.index("\n#!") + 1]
        if "python" in first:
            return "py"
        if "node" in first:
            return "js"
        if "bash" in first:
            return "sh"
        if "ruby" in first:
            return "rb"
        if "php" in first:
            return "php"

    # Heuristics
    if "print(" in code or "import " in code or "def " in code or "class " in code:
        return "py"
    if "function" in code or "const " in code or "let " in code or "=>" in code or "console.log" in code:
        return "js"
    if "puts " in code or "def " in code or "end" in code or "require '" in code:
        return "rb"
    if "<?php" in code or "$_" in code or "echo " in code:
        return "php"
    if "#!/bin/bash" in code or "#!/bin/sh" in code or ("echo " in code and "$" in code):
        return "sh"
    if "local " in code and "function" in code:
        return "lua"
    if "#include" in code and "int main(" in code:
        return "c"

    return "py"  # default to Python


# ─── Code runner tool ────────────────────────────────────────────────────────

class CodeRunner(Tool):
    """Execute code in multiple languages with sandboxing and timeout."""

    def __init__(self, workspace_dir: Path | str | None = None, timeout: int = 30):
        self.workspace_dir = Path(workspace_dir or os.path.expanduser("~/nanobot_workspace"))
        self.timeout = timeout
        self._blacklist = [
            "fork",
            "exec",
            "subprocess",
            "os.system",
            "import os",
            "from os",
            "sys.exit",
            "os._exit",
            "socket.socket",
            "import socket",
            "import subprocess",
            "__import__",
            "eval(",
            "exec(",
        ]

    @property
    def name(self) -> str:
        return "run_code"

    @property
    def description(self) -> str:
        return (
            "Execute code in multiple languages: python, javascript/node, ruby, php, bash, lua, c. "
            "Args: code (str), language (str, auto-detected if omitted). "
            "Returns stdout, stderr, exit code, and execution time."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The code to execute"},
                "language": {"type": "string", "description": "Language: py, js, rb, php, sh, lua, c. Auto-detected if omitted."},
                "timeout": {"type": "integer", "description": "Max seconds (default 30)", "default": 30},
            },
            "required": ["code"],
        }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        code = args.get("code", "")
        language = args.get("language", "auto")
        timeout = int(args.get("timeout", self.timeout))

        if not code.strip():
            return ToolResult(success=False, error="No code provided")

        # Auto-detect
        if language == "auto":
            language = detect_language(code)

        ext, cmd = LANG_MAP.get(language, ("py", "python3"))
        lang_display = language.upper()

        # Security: basic blacklist
        for item in self._blacklist:
            if item in code:
                logger.warning(f"Blocked suspicious code pattern: {item}")
                return ToolResult(
                    success=False,
                    content=f"⛔ Security block: pattern '{item}' is not allowed.",
                )

        start_time = time.time()

        try:
            result = await self._run_code(code, ext, cmd, timeout)
            elapsed = time.time() - start_time

            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr

            # Format result
            if exit_code == 0:
                output = stdout or "(no output)"
                return ToolResult(
                    content=f"🟢 *{lang_display}* — `{elapsed:.2f}s`\n\n```\n{output}\n```",
                )
            else:
                error_out = stderr or stdout or "(no error output)"
                return ToolResult(
                    success=False,
                    content=f"🔴 *{lang_display}* — exit `{exit_code}` — `{elapsed:.2f}s`\n\n```\n{error_out}\n```",
                )

        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                content=f"⏱️ Timed out after {timeout}s",
            )
        except Exception as e:
            logger.error(f"Code runner error: {e}")
            return ToolResult(success=False, content=f"Error: {e}")

    async def _run_code(self, code: str, ext: str, cmd: str, timeout: int):
        """Run code in a temp file with timeout."""
        fd, path = tempfile.mkstemp(suffix=f".{ext}")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(code)

            # C needs compilation first
            if ext == "c":
                exe_path = path + ".bin"
                compile_r = subprocess.run(
                    [cmd, path, "-o", exe_path, "-lm"],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                if compile_r.returncode != 0:
                    return compile_r
                return subprocess.run(
                    [exe_path],
                    capture_output=True,
                    text=True,
                    timeout=max(timeout - 5, 1),
                )

            return subprocess.run(
                [cmd, path] if ext != "sh" else ["/bin/bash", path],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        finally:
            try:
                os.unlink(path)
                if ext == "c":
                    try:
                        os.unlink(path + ".bin")
                    except:
                        pass
            except:
                pass

    def _detect_language(self, code: str) -> str:
        return detect_language(code)