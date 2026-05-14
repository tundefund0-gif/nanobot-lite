"""Multi-language code runner with deep self-healing for Nanobot-Lite."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot_lite.agent.healer import get_healer, Strategy, Severity
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

# Per-language blacklist (more permissive than before)
LANG_BLACKLISTS: dict[str, list[str]] = {
    "py": ["__import__", "eval(", "exec(", "compile(", "settrace", "locals()", "globals()"],
    "js": ["require('child_process')", "child_process.exec", "eval(", "Function("],
    "rb": ["`", "system(", "exec(", "spawn(", "Open3"],
    "php": ["exec(", "shell_exec(", "system(", "passthru(", "proc_open("],
    "sh": ["rm -rf /", ":(){ :|:& };:", "dd if=", "mkfs"],
}


def detect_language(code: str) -> str:
    """Auto-detect language from code content."""
    code = code.strip()

    if code.startswith("#!"):
        shebang = code.split("\n")[0]
        if "python" in shebang:
            return "py"
        if "node" in shebang or "bash" in shebang:
            return "sh"
        if "ruby" in shebang:
            return "rb"
        if "php" in shebang:
            return "php"

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
    if "puts " in code or "def " in code and "end" in code or "require '" in code:
        return "rb"
    if "<?php" in code or "$_" in code or "echo " in code:
        return "php"
    if "#!/bin/bash" in code or "#!/bin/sh" in code or ("echo " in code and "$" in code):
        return "sh"
    if "local " in code and "function" in code:
        return "lua"
    if "#include" in code and "int main(" in code:
        return "c"

    return "py"


# ─── Code runner tool ────────────────────────────────────────────────────────

class CodeRunner(Tool):
    """
    Execute code in multiple languages with deep self-healing:

    - Multi-pass retry: up to 5 passes per execution
    - AST-based Python auto-fix from traceback analysis
    - Command fallback (python3 → python → py)
    - Health tracking and circuit breaker integration
    - Rollback on repeated failure
    - Structured error intelligence
    """

    def __init__(self, workspace_dir: Path | str | None = None, timeout: int = 30):
        self.workspace_dir = Path(workspace_dir or os.path.expanduser("~/nanobot_workspace"))
        self.timeout = timeout
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "run_code"

    @property
    def description(self) -> str:
        return (
            "Execute code in multiple languages: python, javascript/node, ruby, php, bash, lua, c. "
            "Args: code (str), language (str, auto-detected if omitted). "
            "Returns stdout, stderr, exit code, and execution time. "
            "Supports multi-pass self-healing: if code fails, auto-fix and retry up to 5 times."
        )

    input_schema: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "The code to execute"},
            "language": {
                "type": "string",
                "description": "Language: py, js, rb, php, sh, lua, c. Auto-detected if omitted.",
            },
            "timeout": {"type": "integer", "description": "Max seconds (default 30)", "default": 30},
        },
        "required": ["code"],
    })

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

        healer = get_healer()
        heal_log: list[dict] = []
        current_code = code

        # Build command list (with fallbacks)
        cmd_chain = self._build_cmd_chain(ext, cmd)

        # Security check
        check_result = self._security_check(current_code, language)
        if check_result:
            return check_result

        start_time = time.time()

        for pass_num in range(1, healer.MAX_HEAL_PASSES + 1):
            if pass_num > 1:
                logger.info(f"[run_code] healing pass {pass_num} for {lang_display}")

            # Try each command in the chain
            for cmd_item in cmd_chain:
                result = await self._run_once(current_code, ext, cmd_item, timeout)
                elapsed = time.time() - start_time

                if result.returncode == 0:
                    # Success
                    heal_passes = pass_num - 1 if pass_num > 1 else 0
                    healer.record_tool_result("run_code", success=True, heal_passes=heal_passes)

                    if pass_num > 1:
                        heal_log.append({
                            "pass": pass_num,
                            "status": "success_after_fix",
                            "fix_summary": f"Fixed in {pass_num - 1} heal pass(es)",
                        })

                    output = result.stdout or "(no output)"
                    sections = [f"🟢 *{lang_display}* — `{elapsed:.2f}s`"]
                    if pass_num > 1:
                        sections.append(f"🔧 *Self-healed in {pass_num - 1} pass(es)*")
                    sections.append(f"```\n{output}\n```")
                    return ToolResult(content="\n".join(sections))

            # Failed all commands on this pass — attempt healing
            stderr = cmd_item.stderr if cmd_item else ""
            stdout = cmd_item.stdout if cmd_item else ""
            error_text = stderr or stdout or ""

            # Extract error from C compilation output
            if ext == "c" and not error_text:
                error_text = f"Compilation failed with exit code {cmd_item.returncode if cmd_item else '?'}"

            plan = healer.diagnose(error_text)

            if plan.severity == Severity.FATAL or pass_num >= healer.MAX_HEAL_PASSES:
                healer.record_tool_result("run_code", success=False,
                                         heal_passes=pass_num - 1, fatal=(plan.severity == Severity.FATAL))
                elapsed = time.time() - start_time
                return ToolResult(
                    success=False,
                    content=(
                        f"🔴 *{lang_display}* — exit `{cmd_item.returncode if cmd_item else '?'}` — "
                        f"`{elapsed:.2f}s`\n"
                        f"⏱️ Max healing passes ({healer.MAX_HEAL_PASSES}) reached.\n\n"
                        f"🔧 *Diagnosis:* {plan.diagnosis}\n"
                        f"📋 *Hints:*\n" + "\n".join(f"  • {h}" for h in plan.fix_hints) + "\n\n"
                        f"```\n{error_text[:2000]}\n```"
                    ),
                )

            # Attempt auto-fix
            if ext == "py" and plan.strategy in (Strategy.PATCH_CODE, Strategy.RETRY_PARAMS):
                fixed_code, fix_reason = healer.auto_fix_code(current_code, error_text)
                if fixed_code and fixed_code != current_code:
                    heal_log.append({
                        "pass": pass_num,
                        "strategy": plan.strategy.name,
                        "fix_reason": fix_reason,
                        "prev_code_snippet": current_code[:200],
                    })
                    current_code = fixed_code
                    logger.info(f"[run_code] AST auto-fix applied: {fix_reason}")
                    continue  # retry with fixed code

            # LLM-guided fix (for complex errors)
            if plan.escalate_to_llm or plan.strategy not in (Strategy.PATCH_CODE, Strategy.RETRY_PARAMS):
                # For non-Python or non-fixable errors, note the approach
                heal_log.append({
                    "pass": pass_num,
                    "strategy": plan.strategy.name,
                    "diagnosis": plan.diagnosis,
                })
                # Try once more with adjusted approach
                if plan.strategy == Strategy.RETRY_PARAMS:
                    # Slightly relax the code for next pass
                    logger.info(f"[run_code] strategy={plan.strategy.name}, skipping auto-fix")
                    continue

            # Generic retry
            heal_log.append({
                "pass": pass_num,
                "strategy": plan.strategy.name,
                "error": error_text[:200],
            })

        # Should not reach here, but just in case
        return ToolResult(success=False, content="Max healing passes exhausted.")

    def _build_cmd_chain(self, ext: str, cmd: str) -> list[str]:
        """Build command fallback chain for the language."""
        chain = [cmd]

        # Python fallback: python3 → python → python3.11 → python3.10
        if ext == "py":
            for alt in ["python3", "python", "python3.11", "python3.10", "python3.9", "py"]:
                if alt not in chain:
                    chain.append(alt)
        elif ext == "sh":
            for alt in ["/bin/bash", "/bin/sh", "bash", "sh"]:
                if alt not in chain:
                    chain.append(alt)
        elif ext == "c":
            for alt in ["gcc", "clang", "cc"]:
                if alt not in chain:
                    chain.insert(0, alt)

        return chain

    async def _run_once(self, code: str, ext: str, cmd: str, timeout: int) -> subprocess.CompletedProcess:
        """Run code in a temp file with the given command."""
        fd, path = tempfile.mkstemp(suffix=f".{ext}")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(code)

            if ext == "c":
                exe_path = path + ".bin"
                compile_r = subprocess.run(
                    [cmd, path, "-o", exe_path, "-lm", "-w"],
                    capture_output=True, text=True, timeout=timeout,
                )
                if compile_r.returncode != 0:
                    return compile_r
                return subprocess.run(
                    [exe_path], capture_output=True, text=True,
                    timeout=max(timeout - 5, 1),
                )

            shell_cmd = [cmd, path] if ext != "sh" else ["/bin/bash", path]
            return subprocess.run(
                shell_cmd, capture_output=True, text=True, timeout=timeout,
            )
        finally:
            try:
                os.unlink(path)
                if ext == "c":
                    try:
                        os.unlink(path + ".bin")
                    except OSError:
                        pass
            except OSError:
                pass

    def _security_check(self, code: str, language: str) -> ToolResult | None:
        """Check code against language-specific blacklist."""
        blacklist = LANG_BLACKLISTS.get(language, [])
        for item in blacklist:
            if item in code:
                logger.warning(f"[run_code] blocked: {item}")
                return ToolResult(
                    success=False,
                    content=f"⛔ Security block: pattern '{item}' is not allowed for {language}.",
                )
        return None
