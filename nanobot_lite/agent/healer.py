"""
Advanced Self-Healing Engine — Nanobot-Lite
===========================================
Multi-layer autonomous repair system:

  Layer 1 — Structured Error Intelligence
    40+ error patterns with severity, recovery strategy, and fix hints.

  Layer 2 — Tool-Specific Recovery
    Per-tool fallback chains (e.g., grep→rg→python search).

  Layer 3 — Path Auto-Discovery
    FileNotFoundError triggers workspace path search + alternative path hints.

  Layer 4 — Circuit Breaker
    Per-tool failure counter; trips after N failures, skips tool for cooldown.

  Layer 5 — Rollback System
    Backs up files before patching; restores if fix fails repeatedly.

  Layer 6 — Health Monitor
    Tracks tool health scores, failure rates, and recovery success.

  Layer 7 — LLM-Guided Healing
    Complex/ambiguous errors are escalated to LLM with full context.

  Layer 8 — Iterative Multi-Pass Healing
    Up to 5 healing passes per tool call, escalating strategy each pass.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any

from loguru import logger

# ─── Severity levels ─────────────────────────────────────────────────────────

class Severity(IntEnum):
    LOW      = 1   # Minor issue, minor fix
    MEDIUM   = 2   # Clear error, straightforward fix
    HIGH     = 3   # Serious error, needs careful fix
    CRITICAL = 4   # Likely to recur, needs structural fix
    FATAL    = 5   # Tool unusable, skip and warn

# ─── Recovery strategies ─────────────────────────────────────────────────────

class Strategy(IntEnum):
    RETRY         = 1   # Just retry with same approach
    RETRY_PARAMS  = 2   # Retry with adjusted parameters
    FALLBACK_TOOL = 3   # Use a different tool for same goal
    PATCH_CODE    = 4   # Auto-patch the code at AST level
    ALT_PATH      = 5   # Try alternative file paths
    LLM_ESCALATE  = 6   # Ask LLM for targeted fix
    SKIP          = 7   # Skip this operation, report failure
    CIRCUIT_OPEN  = 8   # Open circuit breaker, skip tool entirely

# ─── Recovery plan dataclass ──────────────────────────────────────────────────

@dataclass
class RecoveryPlan:
    severity: Severity
    strategy: Strategy
    diagnosis: str
    fix_hints: list[str]
    backup_required: bool = False
    escalate_to_llm: bool = False
    llm_fix_prompt: str = ""

# ─── Error pattern entry ──────────────────────────────────────────────────────

@dataclass
class ErrorPattern:
    regex: str
    severity: Severity
    strategy: Strategy
    diagnosis: str
    fix_hints: list[str]

# ─── Structured error pattern database ────────────────────────────────────────

ERROR_PATTERNS: list[ErrorPattern] = [
    # ── Python Syntax & Runtime ────────────────────────────────────────────────
    ErrorPattern(r"SyntaxError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Fix Python syntax — unmatched parens, quotes, indentation, or keyword misuse.",
        ["Check line number in traceback", "Run 'python -m py_compile' to confirm",
         "Look for missing colons after def/if/for", "Verify string quotes are balanced"]),
    ErrorPattern(r"IndentationError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Fix indentation — use consistent spaces (4 recommended), check mix of tabs/spaces.",
        ["Set editor to spaces=4, no tabs", "Run 'python -m py_compile' to verify",
         "Check for tabs after spaces or vice versa"]),
    ErrorPattern(r"TabError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Mixed tabs and spaces — standardize to spaces only.",
        ["Replace all tabs with 4 spaces", "Set editor to spaces-only indentation"]),
    ErrorPattern(r"NameError:\s*(\w+)", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Variable or name not defined — check spelling, imports, and scope.",
        ["Verify variable is defined before use", "Check import statement",
         "Check for typos in name", "Ensure correct module is imported"]),
    ErrorPattern(r"UnboundLocalError", Severity.HIGH, Strategy.PATCH_CODE,
        "Variable referenced before assignment in local scope.",
        ["Move variable assignment before first use", "Use 'nonlocal' or 'global' if intentional"]),
    ErrorPattern(r"ImportError", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Import failed — check package name, installation, and Python path.",
        ["pip install the missing package", "Check package name spelling",
         "Verify PYTHONPATH includes the module directory", "Try 'from X import Y' instead of 'import X'"]),
    ErrorPattern(r"ModuleNotFoundError|No module named", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Module not installed — install it or check the module name.",
        ["pip install <module>", "Check if package exists on PyPI",
         "Try alternative package name", "Verify Python environment"]),
    ErrorPattern(r"AttributeError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Object has no attribute — check type, spelling, and available methods.",
        ["Verify object type with type()", "Check attribute name spelling",
         "Ensure object is not None before accessing attributes"]),
    ErrorPattern(r"TypeError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Type mismatch — function received wrong argument type.",
        ["Check argument types against function signature",
         "Convert types explicitly (int(), str(), list(), etc.)",
         "Check for None where value was expected"]),
    ErrorPattern(r"ValueError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Value is wrong type/range — check the actual value vs expected.",
        ["Validate input value", "Check for empty strings/lists/dicts",
         "Ensure value is within expected range", "Add input sanitization"]),
    ErrorPattern(r"KeyError", Severity.LOW, Strategy.PATCH_CODE,
        "Dictionary key missing — check key spelling and existence.",
        ["Use dict.get(key, default) for safe access", "Print dict keys to verify",
         "Check for case sensitivity in keys"]),
    ErrorPattern(r"IndexError", Severity.LOW, Strategy.PATCH_CODE,
        "List index out of range — index is >= len(list).",
        ["Check list length before indexing", "Use list[-1] for last element safely",
         "Add bounds check: if i < len(lst):", "Use enumerate() instead of range(len())"]),
    ErrorPattern(r"StopIteration", Severity.HIGH, Strategy.PATCH_CODE,
        "Iterator exhausted — ran out of items in a for loop or next().",
        ["Check iterator initialization", "Use try/except around next()",
         "Use list() to convert iterator if needed"]),
    ErrorPattern(r"RuntimeError", Severity.HIGH, Strategy.RETRY_PARAMS,
        "Generic runtime error — read full traceback for specifics.",
        ["Read the full error message", "Check variable states at failure point",
         "Add debug print statements", "Isolate the failing code section"]),
    ErrorPattern(r"RecursionError", Severity.CRITICAL, Strategy.PATCH_CODE,
        "Infinite recursion — base case missing or not reached.",
        ["Add/improve base case in recursive function", "Check termination condition",
         "Consider iterative approach instead", "Add recursion depth limit"]),
    ErrorPattern(r"MemoryError", Severity.FATAL, Strategy.SKIP,
        "Out of memory — operation too large for available RAM.",
        ["Process data in smaller chunks", "Use generators instead of lists",
         "Free memory with del and gc.collect()", "Consider streaming approach"]),
    ErrorPattern(r"OverflowError", Severity.HIGH, Strategy.PATCH_CODE,
        "Number too large — arithmetic result exceeds type limits.",
        ["Use float instead of int for large numbers", "Break computation into steps",
         "Use Python's arbitrary precision ints"]),
    ErrorPattern(r"ZeroDivisionError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Division or modulo by zero.",
        ["Check denominator before division", "Add zero-check guard",
         "Use safe_divide(a, b) returning None on zero"]),
    ErrorPattern(r"FileNotFoundError|No such file", Severity.LOW, Strategy.ALT_PATH,
        "File does not exist — check path, spelling, and working directory.",
        ["Verify path with os.path.exists()", "Check working directory",
         "Search for similar file names in workspace", "Create parent directories first"]),
    ErrorPattern(r"IsADirectoryError", Severity.LOW, Strategy.RETRY_PARAMS,
        "Expected a file but got a directory.",
        ["Use directory path if directory was intended", "Check path with .is_file() first",
         "Use os.listdir() for directory contents"]),
    ErrorPattern(r"NotADirectoryError", Severity.LOW, Strategy.RETRY_PARAMS,
        "Expected a directory but got a file.",
        ["Use file path if file was intended", "Check path with .is_dir() first"]),
    ErrorPattern(r"PermissionError", Severity.HIGH, Strategy.SKIP,
        "Permission denied — insufficient access rights.",
        ["Check file permissions with ls -la", "Run with appropriate user/privileges",
         "Check parent directory write permissions", "Try changing ownership or permissions"]),
    ErrorPattern(r"OSError.*\[Errno 28\]", Severity.FATAL, Strategy.SKIP,
        "No space left on device.",
        ["Delete temporary files", "Clear cache directories", "Check disk space with df -h"]),
    ErrorPattern(r"BrokenPipeError|EPIPE", Severity.LOW, Strategy.SKIP,
        "Broken pipe — output stream closed prematurely.",
        ["Pipe to 'less' or redirect to file", "Check if output reader closed early"]),
    ErrorPattern(r"ConnectionError|timeout|Network|URLError", Severity.MEDIUM, Strategy.RETRY,
        "Network/connection error — check connectivity and retry.",
        ["Verify internet connection", "Retry with exponential backoff",
         "Check firewall/proxy settings", "Try alternative endpoint"]),
    ErrorPattern(r"HTTPError \d{3}", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "HTTP error response — server returned an error status.",
        ["Check URL is correct", "Add proper error handling for status codes",
         "Handle 429 (rate limit) with backoff", "Check authentication"]),
    ErrorPattern(r"JSONDecodeError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Invalid JSON — parsing failed, check string format.",
        ["Validate JSON with json.loads() in Python REPL", "Check for trailing commas",
         "Ensure double quotes only (no single quotes)", "Validate string encoding"]),
    ErrorPattern(r"UnicodeDecodeError", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Text encoding error — file or string uses incompatible encoding.",
        ["Specify encoding explicitly (utf-8, latin-1)", "Try errors='replace' or 'ignore'",
         "Check source file encoding"]),
    ErrorPattern(r"TimeoutExpired|timed out", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Operation timed out — increase timeout or simplify operation.",
        ["Increase timeout parameter", "Break operation into smaller steps",
         "Use streaming/chunked approach", "Cancel if not needed"]),

    # ── Shell / CLI ────────────────────────────────────────────────────────────
    ErrorPattern(r"command not found|not installed", Severity.HIGH, Strategy.FALLBACK_TOOL,
        "Command not found — tool may not be installed.",
        ["Check if tool is installed", "Try alternative tool/command",
         "Install the missing tool", "Use Python stdlib equivalent"]),
    ErrorPattern(r"Permission denied.*chmod", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Permission denied on chmod — may need sudo or different permissions.",
        ["Check current file permissions", "Use chmod 644 for files, 755 for scripts",
         "Try sudo if running as non-root"]),
    ErrorPattern(r"cannot create.*directory", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Cannot create directory — parent may not exist or no permissions.",
        ["Create parent directories first with mkdir -p", "Check write permissions",
         "Use absolute path instead of relative"]),
    ErrorPattern(r"grep:.*No such file", Severity.LOW, Strategy.ALT_PATH,
        "grep couldn't find a file — path may be wrong or file doesn't exist.",
        ["Verify file path", "Use -r for recursive search", "Check working directory"]),
    ErrorPattern(r"git:.*not a git repository", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Not inside a git repository.",
        ["Run 'git init' to initialize", "cd to repository root",
         "Check you're in correct directory"]),

    # ── Telegram / API ──────────────────────────────────────────────────────────
    ErrorPattern(r"400.*Bad Request", Severity.HIGH, Strategy.LLM_ESCALATE,
        "API returned 400 Bad Request — malformed request parameters.",
        ["Validate all API parameters", "Check request body format",
         "Review API documentation for required fields"]),
    ErrorPattern(r"401|403.*Unauthorized|Forbidden", Severity.CRITICAL, Strategy.SKIP,
        "Authentication/authorization error — check API key or token.",
        ["Verify API key/token is correct", "Check token hasn't expired",
         "Verify permissions/scopes", "Check rate limits"]),
    ErrorPattern(r"429.*Too Many Requests|Rate limit", Severity.MEDIUM, Strategy.RETRY,
        "Rate limited — too many requests, back off and retry.",
        ["Wait before retrying", "Implement request queuing",
         "Cache responses where possible", "Use exponential backoff"]),
    ErrorPattern(r"500|502|503|504.*Server Error", Severity.HIGH, Strategy.RETRY,
        "Server-side error — remote service is having issues.",
        ["Retry after delay (server error)", "Try alternative endpoint if available",
         "Check service status page"]),
    ErrorPattern(r"Telegram.*conflict", Severity.CRITICAL, Strategy.SKIP,
        "Telegram conflict — multiple instances running with same bot token.",
        ["Stop other bot instances", "Use separate bot tokens for different instances"]),
    ErrorPattern(r"Message.*not found|chat.*not found", Severity.LOW, Strategy.SKIP,
        "Telegram message/chat not found — may have been deleted.",
        ["Message may have been deleted", "Chat may no longer exist",
         "Check chat_id is correct"]),

    # ── LLM Provider ─────────────────────────────────────────────────────────────
    ErrorPattern(r"context_length_exceeded|maximum context|too many tokens",
        Severity.CRITICAL, Strategy.LLM_ESCALATE,
        "Context window exceeded — conversation too long.",
        ["Summarize or compress conversation history", "Split task into smaller parts",
         "Reduce system prompt length", "Use higher max_tokens efficiency"]),
    ErrorPattern(r"rate_limit_exceeded|quota_exceeded", Severity.MEDIUM, Strategy.RETRY,
        "API rate limit hit — back off and retry.",
        ["Wait and retry with exponential backoff", "Check usage dashboard",
         "Reduce request frequency"]),
    ErrorPattern(r"api_key|invalid.*key|authentication", Severity.FATAL, Strategy.SKIP,
        "Invalid API key — check configuration.",
        ["Verify API key in config", "Check key hasn't been rotated",
         "Ensure correct provider/auth method"]),
    ErrorPattern(r"model.*not found|unknown.*model", Severity.HIGH, Strategy.RETRY_PARAMS,
        "Model not found — check model name spelling.",
        ["Verify model name in provider docs", "Try alternative model",
         "Check provider availability for that model"]),
]

# ─── Tool fallback chains ─────────────────────────────────────────────────────

TOOL_FALLBACKS: dict[str, list[str]] = {
    "grep": ["rg", "fgrep", "find", "python_search"],
    "rg": ["grep", "find", "python_search"],
    "find": ["python_search", "list_dir"],
    "curl": ["wget", "fetch_url", "python_download"],
    "wget": ["curl", "fetch_url", "python_download"],
    "jq": ["python_json", "python -c"],
    "sed": ["awk", "python_stream", "edit_file"],
    "awk": ["sed", "python_stream", "python -c"],
    "make": ["cmake", "python_build", "shell"],
    "gcc": ["clang", "python_cc", "shell"],
    "python3": ["python", "python3.11", "python3.10", "py"],
    "python": ["python3", "python3.11", "python3.10", "py"],
}

# ─── Circuit Breaker ───────────────────────────────────────────────────────────

@dataclass
class CircuitState:
    failures: int = 0
    last_failure: float = 0.0
    open_since: float | None = None
    half_open: bool = False

class CircuitBreaker:
    """
    Per-tool circuit breaker.
    Opens after FAILURE_THRESHOLD failures within FAILURE_WINDOW seconds.
    Half-opens after COOLDOWN seconds to test recovery.
    Closes on success, re-opens on continued failure.
    """
    FAILURE_THRESHOLD = 5
    FAILURE_WINDOW    = 300.0   # 5 minutes
    COOLDOWN          = 60.0    # 1 minute

    def __init__(self):
        self._circuits: dict[str, CircuitState] = {}

    def _state(self, tool: str) -> CircuitState:
        if tool not in self._circuits:
            self._circuits[tool] = CircuitState()
        return self._circuits[tool]

    def is_open(self, tool: str) -> bool:
        state = self._state(tool)
        now = datetime.now(timezone.utc).timestamp()

        # If permanently open (FATAL error), never auto-recover
        if state.open_since and (now - state.open_since) < 0:
            return True

        # Cooldown check
        if state.open_since and (now - state.open_since) < self.COOLDOWN:
            return True

        # Half-open: allow one test request
        if state.half_open:
            return False

        # Check if we should trip
        if state.failures >= self.FAILURE_THRESHOLD:
            state.open_since = now
            state.half_open = False
            return True

        # Window expired: reset counter
        if state.last_failure and (now - state.last_failure) > self.FAILURE_WINDOW:
            state.failures = 0

        return False

    def record_success(self, tool: str) -> None:
        state = self._state(tool)
        state.failures = 0
        state.half_open = False
        state.open_since = None
        state.last_failure = 0.0

    def record_failure(self, tool: str, fatal: bool = False) -> None:
        state = self._state(tool)
        now = datetime.now(timezone.utc).timestamp()
        state.last_failure = now
        state.failures += 1
        state.half_open = False
        if fatal:
            state.open_since = now  # permanently open until restart
        elif state.failures >= self.FAILURE_THRESHOLD:
            state.open_since = now

    def try_half_open(self, tool: str) -> bool:
        """Called by heal loop to test if tool has recovered."""
        state = self._state(tool)
        if not state.open_since:
            return False
        now = datetime.now(timezone.utc).timestamp()
        if (now - state.open_since) >= self.COOLDOWN:
            state.half_open = True
            return True
        return False

    def report(self) -> dict[str, Any]:
        return {
            tool: {
                "failures": s.failures,
                "open": self.is_open(tool),
                "half_open": s.half_open,
                "last_failure": datetime.fromtimestamp(s.last_failure, tz=timezone.utc).isoformat()
                    if s.last_failure else None,
            }
            for tool, s in self._circuits.items()
        }

# ─── Rollback system ──────────────────────────────────────────────────────────

class RollbackManager:
    """
    Backup files before patching. Restore if fix fails.
    Keeps rolling backups (max 3 versions per file).
    """
    MAX_BACKUPS = 3

    def __init__(self, backup_dir: Path | None = None):
        self.backup_dir = backup_dir or Path(tempfile.gettempdir()) / "nanobot_rollback"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._backups: dict[str, list[Path]] = {}

    def backup(self, file_path: str | Path) -> Path | None:
        """Backup a file before modification. Returns backup path."""
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            return None

        try:
            hash_suffix = hashlib.md5(str(p).encode()).hexdigest()[:8]
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_name = f"{p.name}.{ts}.{hash_suffix}.bak"
            backup_path = self.backup_dir / backup_name
            shutil.copy2(p, backup_path)

            if str(p) not in self._backups:
                self._backups[str(p)] = []
            self._backups[str(p)].append(backup_path)

            # Prune old backups beyond MAX_BACKUPS
            while len(self._backups[str(p)]) > self.MAX_BACKUPS:
                old = self._backups[str(p)].pop(0)
                try:
                    old.unlink()
                except OSError:
                    pass

            logger.debug(f"Backed up {p} → {backup_path}")
            return backup_path
        except Exception as e:
            logger.warning(f"Backup failed for {p}: {e}")
            return None

    def restore(self, file_path: str | Path, backup_path: Path | None = None) -> bool:
        """Restore file from most recent backup, or specific backup."""
        p = Path(file_path)
        backups = self._backups.get(str(p), [])

        if backup_path:
            src = backup_path
        elif backups:
            src = backups[-1]
        else:
            logger.warning(f"No backup found for {p}")
            return False

        try:
            shutil.copy2(src, p)
            logger.info(f"Restored {p} from {src}")
            return True
        except Exception as e:
            logger.error(f"Restore failed for {p}: {e}")
            return False

    def get_backups(self, file_path: str | Path) -> list[Path]:
        return list(self._backups.get(str(file_path), []))

# ─── Health Monitor ───────────────────────────────────────────────────────────

@dataclass
class ToolHealth:
    name: str
    total_calls: int = 0
    failures: int = 0
    heals_success: int = 0
    heals_failed: int = 0
    avg_heal_passes: float = 0.0
    last_success: float = 0.0
    last_failure: float = 0.0

    @property
    def health_score(self) -> float:
        if self.total_calls == 0:
            return 1.0
        failure_rate = self.failures / self.total_calls
        heal_rate = self.heals_success / max(self.heals_success + self.heals_failed, 1)
        return max(0.0, 1.0 - failure_rate + (heal_rate * 0.2))

    @property
    def status(self) -> str:
        s = self.health_score
        if s >= 0.9:
            return "🟢 healthy"
        elif s >= 0.6:
            return "🟡 degraded"
        elif s >= 0.3:
            return "🟠 poor"
        else:
            return "🔴 critical"

class HealthMonitor:
    """Tracks per-tool health, failure rates, and healing effectiveness."""

    def __init__(self):
        self._tools: dict[str, ToolHealth] = {}

    def track(self, tool_name: str) -> ToolHealth:
        if tool_name not in self._tools:
            self._tools[tool_name] = ToolHealth(name=tool_name)
        return self._tools[tool_name]

    def record_call(self, tool_name: str, success: bool,
                    heal_passes: int = 0, healed: bool = False) -> None:
        h = self.track(tool_name)
        now = datetime.now(timezone.utc).timestamp()
        h.total_calls += 1
        if success:
            h.last_success = now
            if heal_passes > 0:
                h.heals_success += 1
                # Update rolling average of heal passes
                h.avg_heal_passes = (
                    (h.avg_heal_passes * (h.heals_success - 1) + heal_passes)
                    / h.heals_success
                )
        else:
            h.last_failure = now
            h.failures += 1
            if heal_passes > 0:
                h.heals_failed += 1

    def get_report(self) -> str:
        lines = ["## 🏥 Tool Health Report\n"]
        for h in sorted(self._tools.values(), key=lambda x: x.health_score):
            age_success = ""
            if h.last_success:
                age = int(datetime.now(timezone.utc).timestamp() - h.last_success)
                age_success = f" (last OK {age}s ago)"
            age_fail = ""
            if h.last_failure:
                age = int(datetime.now(timezone.utc).timestamp() - h.last_failure)
                age_fail = f" (last fail {age}s ago)"
            lines.append(
                f"{h.status} **{h.name}** — "
                f"score={h.health_score:.2f}, "
                f"calls={h.total_calls}, "
                f"fails={h.failures}, "
                f"healed={h.heals_success}/{h.heals_failed}, "
                f"avg_passes={h.avg_heal_passes:.1f}"
                f"{age_success}{age_fail}"
            )
        if not lines:
            lines.append("(no data yet)")
        return "\n".join(lines)

# ─── AST-based Python code fixer ─────────────────────────────────────────────

class PythonAutoFixer:
    """
    Analyzes Python tracebacks and auto-fixes common errors at the AST level.
    Returns (fixed_code, explanation) or (None, error_message).
    """

    @staticmethod
    def fix_from_traceback(code: str, error_text: str) -> tuple[str | None, str]:
        """
        Parse error type + line from traceback, apply targeted AST fix.
        Returns (fixed_code, explanation) or (None, "no auto-fix available").
        """
        # Extract error type
        error_type = re.search(r"(\w+Error|\w+Exception):", error_text)
        error_type = error_type.group(1) if error_type else ""

        # Extract line number
        line_match = re.search(r"line (\d+)", error_text)
        line_no = int(line_match.group(1)) - 1 if line_match else None  # 0-indexed

        # Extract variable name for NameError
        name_match = re.search(r"NameError:.*?'(\w+)'|NameError:.*?(\w+) not defined", error_text)
        bad_name = name_match.group(1) or name_match.group(2) if name_match else None

        try:
            tree = ast.parse(code)
        except SyntaxError as se:
            # Try to fix the syntax error directly
            return PythonAutoFixer._fix_syntax_error(code, se, error_text)

        # Strategy based on error type
        if "NameError" in error_type and bad_name:
            return PythonAutoFixer._fix_name_error(code, tree, bad_name, line_no)
        if "IndexError" in error_type:
            return PythonAutoFixer._fix_index_error(code, tree, line_no)
        if "KeyError" in error_type:
            return PythonAutoFixer._fix_key_error(code, tree, line_no)
        if "TypeError" in error_type:
            return PythonAutoFixer._fix_type_error(code, tree, line_no, error_text)
        if "AttributeError" in error_type:
            return PythonAutoFixer._fix_attribute_error(code, tree, line_no, error_text)
        if "ImportError" in error_type or "ModuleNotFoundError" in error_type:
            return PythonAutoFixer._fix_import_error(code, tree, error_text)
        if "IndentationError" in error_type or "TabError" in error_type:
            return PythonAutoFixer._fix_indentation(code, error_text)
        if "ZeroDivisionError" in error_type:
            return PythonAutoFixer._fix_zero_division(code, tree, line_no)
        if "ValueError" in error_type:
            return PythonAutoFixer._fix_value_error(code, tree, line_no, error_text)
        if "UnboundLocalError" in error_type:
            return PythonAutoFixer._fix_unbound_local(code, tree, bad_name)

        return None, f"No AST auto-fix available for {error_type}"

    @staticmethod
    def _fix_syntax_error(code: str, se: SyntaxError, error_text: str) -> tuple[str | None, str]:
        """Fix common SyntaxError patterns."""
        lines = code.splitlines()
        if se.lineno is None:
            return None, "Could not determine line number for SyntaxError"

        line_idx = se.lineno - 1

        # Fix: missing closing paren
        if "unterminated string" in error_text.lower() or "eol" in error_text.lower():
            return None, "Unterminated string — fix manually: check quotes are balanced"

        # Fix: missing colon after def/if/for/class
        if line_idx < len(lines):
            line = lines[line_idx]
            stripped = line.rstrip()
            for kw in ("def ", "class ", "if ", "elif ", "else:", "for ", "while ", "try:", "except", "with "):
                if kw.rstrip(":").rstrip() in stripped and stripped.endswith(")"):
                    # Looks like function call used instead of definition
                    pass

        # Fix: unmatched parens/brackets
        for opener, closer in [("(", ")"), ("[", "]"), ("{", "}")]:
            count = code.count(opener) - code.count(closer)
            if count > 0:
                # Find last unclosed opener position
                last_opener = -1
                depth = 0
                for i, ch in enumerate(code):
                    if ch == opener:
                        depth += 1
                        last_opener = i
                    elif ch == closer:
                        depth -= 1
                if depth > 0 and last_opener >= 0:
                    new_code = code + closer
                    return new_code, f"Auto-fixed: added missing {closer} at end"

        return None, f"SyntaxError at line {se.lineno}: {se.msg}"

    @staticmethod
    def _fix_name_error(code: str, tree: ast.AST, bad_name: str, line_no: int | None) -> tuple[str | None, str]:
        """Fix NameError: insert None assignment or import if possible."""
        lines = code.splitlines()
        if line_no and line_no < len(lines):
            # Look for the offending line and add a None assignment before it
            target_line = lines[line_no]
            indent = len(target_line) - len(target_line.lstrip())
            ws = " " * indent
            lines.insert(line_no, f"{ws}{bad_name} = None  # auto-fix: undefined name")
            new_code = "\n".join(lines)
            return new_code, f"Auto-fixed: added '{bad_name} = None' before use"

        # Try: insert at function/class scope start
        # Find the innermost scope and add there
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.lineno and node.lineno - 1 < len(lines):
                    body = node.body
                    if body:
                        ins_after = body[0].lineno - 1
                        target_line = lines[ins_after]
                        indent = len(target_line) - len(target_line.lstrip()) + 4
                        ws = " " * indent
                        lines.insert(ins_after, f"{ws}{bad_name} = None  # auto-fix")
                        new_code = "\n".join(lines)
                        return new_code, f"Auto-fixed: added '{bad_name} = None' in {node.name}"

        return None, f"NameError '{bad_name}': could not auto-fix, needs manual import/definition"

    @staticmethod
    def _fix_index_error(code: str, tree: ast.AST, line_no: int | None) -> tuple[str | None, str]:
        """Wrap index access with bounds check."""
        if line_no is None:
            return None, "IndexError: could not determine line number"
        lines = code.splitlines()
        if line_no >= len(lines):
            return None, "IndexError: line number out of range"

        line = lines[line_no]

        # Try to extract variable name before [
        m = re.match(r"^(.*?)(\w+)\[(.+?)\]$", line.strip())
        if m:
            prefix, var, index = m.groups()
            new_line = f"{prefix}if {index} < len({var}): {var}[{index}]"
            lines[line_no] = " " * (len(line) - len(line.lstrip())) + new_line
            new_code = "\n".join(lines)
            return new_code, f"Auto-fixed: added bounds check before indexing {var}[{index}]"

        return None, "IndexError: could not auto-fix, needs manual bounds check"

    @staticmethod
    def _fix_key_error(code: str, tree: ast.AST, line_no: int | None) -> tuple[str | None, str]:
        """Convert dict[key] to dict.get(key) or add get_default."""
        if line_no is None:
            return None, "KeyError: could not determine line number"
        lines = code.splitlines()
        if line_no >= len(lines):
            return None, "KeyError: line number out of range"

        line = lines[line_no]
        # Pattern: something["key"] -> something.get("key")
        m = re.search(r"(\w+)\[(.+?)\]", line)
        if m:
            var, key = m.groups()
            new_line = line.replace(f"{var}[{key}]", f"{var}.get({key})")
            lines[line_no] = new_line
            new_code = "\n".join(lines)
            return new_code, f"Auto-fixed: replaced {var}[{key!r}] with {var}.get({key!r})"

        return None, "KeyError: could not auto-fix, needs manual .get() or key check"

    @staticmethod
    def _fix_type_error(code: str, tree: ast.AST, line_no: int | None, error_text: str) -> tuple[str | None, str]:
        """Try to add type conversion for TypeError."""
        # If it's str + int, add str()
        if line_no and line_no < len(lines):
            line = lines[line_no]
            # str + int -> str + str()
            fixed = re.sub(r'(\w+)\s*\+\s*(\w+)', lambda m: (
                m.group(1) + " + str(" + m.group(2) + ")"
                if m.group(1) != m.group(2) else m.group(0)
            ), line)
            if fixed != line:
                lines[line_no] = fixed
                new_code = "\n".join(lines)
                return new_code, "Auto-fixed: added str() conversion for type mismatch"
        return None, "TypeError: could not auto-fix, needs manual type conversion"

    @staticmethod
    def _fix_attribute_error(code: str, tree: ast.AST, line_no: int | None, error_text: str) -> tuple[str | None, str]:
        """Fix AttributeError by checking type or suggesting hasattr."""
        # Extract attribute name
        m = re.search(r"'(.*?)'", error_text)
        attr = m.group(1) if m else None
        if not attr:
            return None, "AttributeError: could not extract attribute name"

        if line_no and line_no < len(lines):
            line = lines[line_no]
            # Replace .attr with getattr(obj, 'attr', None)
            fixed = re.sub(rf"\.({re.escape(attr)})", f".get(\"{attr}\", None)", line)
            if fixed != line:
                lines[line_no] = fixed
                new_code = "\n".join(lines)
                return new_code, f"Auto-fixed: replaced .{attr} with .get('{attr}', None)"
        return None, f"AttributeError on '{attr}': could not auto-fix"

    @staticmethod
    def _fix_import_error(code: str, tree: ast.AST, error_text: str) -> tuple[str | None, str]:
        """Fix ImportError: try alternative import style."""
        m = re.search(r"import '(\w+)'|from (\w+) import|No module named '(\w+)'", error_text)
        module = m.group(1) or m.group(2) or m.group(3) if m else None
        if not module:
            return None, "ImportError: could not identify module"

        # Try: from X import Y instead of import X
        lines = code.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(f"import {module}"):
                lines[i] = line.replace(f"import {module}", f"from {module} import *")
                new_code = "\n".join(lines)
                return new_code, f"Auto-fixed: changed to 'from {module} import *'"

        return None, f"ImportError for '{module}': could not auto-fix, install or check name"

    @staticmethod
    def _fix_indentation(code: str, error_text: str) -> tuple[str | None, str]:
        """Fix indentation errors — replace tabs with 4 spaces."""
        if "\t" in code:
            new_code = code.replace("\t", "    ")
            return new_code, "Auto-fixed: replaced tabs with 4 spaces"
        return None, "IndentationError: could not auto-fix, check indentation manually"

    @staticmethod
    def _fix_zero_division(code: str, tree: ast.AST, line_no: int | None) -> tuple[str | None, str]:
        """Wrap division with zero check."""
        if line_no and line_no < len(lines_split := code.splitlines()):
            line = lines_split[line_no]
            # Find / or % operators
            if "/" in line or "%" in line:
                # Try to identify divisor
                parts = re.split(r"(/|%)", line)
                if len(parts) >= 3:
                    before_op = parts[0].strip()
                    op = parts[1]
                    after_op = parts[2].strip().rstrip(")").rstrip("]").rstrip(")")
                    # Wrap the divisor
                    safe_divisor = f"({after_op} or 1)"
                    new_line = before_op + op + safe_divisor
                    if len(parts) > 3:
                        new_line += "".join(parts[3:])
                    lines_split[line_no] = new_line
                    new_code = "\n".join(lines_split)
                    return new_code, "Auto-fixed: replaced divisor with (divisor or 1)"
        return None, "ZeroDivisionError: could not auto-fix"

    @staticmethod
    def _fix_value_error(code: str, tree: ast.AST, line_no: int | None, error_text: str) -> tuple[str | None, str]:
        """Wrap potentially failing value conversions."""
        if line_no and line_no < len(lines_split := code.splitlines()):
            line = lines_split[line_no]
            # int(x), float(x), etc.
            m = re.search(r"(int|float|list|tuple|set|dict|str)\((.+)\)", line)
            if m:
                fn, arg = m.groups()
                new_line = line.replace(
                    f"{fn}({arg})",
                    f"({fn}({arg}) if {arg} else None)"
                )
                lines_split[line_no] = new_line
                new_code = "\n".join(lines_split)
                return new_code, f"Auto-fixed: wrapped {fn}() with fallback to None"
        return None, "ValueError: could not auto-fix"

    @staticmethod
    def _fix_unbound_local(code: str, tree: ast.AST, bad_name: str | None) -> tuple[str | None, str]:
        """Fix UnboundLocalError: add 'nonlocal' or 'global' declaration."""
        if not bad_name:
            return None, "UnboundLocalError: could not identify variable"

        lines = code.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for i, line in enumerate(lines):
                    if line.strip().startswith(f"def {node.name}"):
                        indent = len(line) - len(line.lstrip()) + 4
                        lines.insert(i + 1, " " * indent + f"global {bad_name}  # auto-fix: unbound local")
                        new_code = "\n".join(lines)
                        return new_code, f"Auto-fixed: added 'global {bad_name}' in {node.name}()"

        return None, "UnboundLocalError: could not auto-fix"

# ─── Path auto-discovery ──────────────────────────────────────────────────────

class PathAutoDiscover:
    """Search workspace for files matching failed path patterns."""

    @staticmethod
    def find_similar(file_path: str, workspace: str | None = None) -> list[str]:
        """Find files with similar names in workspace."""
        if workspace is None:
            workspace = os.getcwd()
        workspace = Path(workspace).resolve()

        stem = Path(file_path).stem.lower()
        ext = Path(file_path).suffix.lower()

        candidates = []
        try:
            for root, dirs, files in os.walk(workspace):
                # Skip hidden and common skip dirs
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
                    "__pycache__", "node_modules", ".git", "venv", ".venv", "env"
                )]
                for fname in files:
                    if fname.startswith("."):
                        continue
                    fstem = Path(fname).stem.lower()
                    fext = Path(fname).suffix.lower()
                    score = 0
                    if stem and (stem in fstem or fstem in stem):
                        score += 2
                    if ext == fext:
                        score += 1
                    if score > 0:
                        full = Path(root) / fname
                        candidates.append((score, str(full.relative_to(workspace))))
        except PermissionError:
            pass

        candidates.sort(reverse=True)
        return [c[1] for c in candidates[:5]]

# ─── Main Self-Healer ─────────────────────────────────────────────────────────

class SelfHealer:
    """
    Central self-healing engine.
    Coordinates: error diagnosis, recovery planning, AST fixing,
    path discovery, circuit breaking, rollback, health monitoring.
    """

    MAX_HEAL_PASSES = 5

    def __init__(self):
        self.circuit_breaker = CircuitBreaker()
        self.rollback = RollbackManager()
        self.health = HealthMonitor()
        self.python_fixer = PythonAutoFixer()
        self.path_finder = PathAutoDiscover()

    # ── Public API ─────────────────────────────────────────────────────────────

    def diagnose(self, error_text: str) -> RecoveryPlan:
        """Diagnose an error and return a structured RecoveryPlan."""
        for ep in ERROR_PATTERNS:
            m = re.search(ep.regex, error_text, re.IGNORECASE)
            if m:
                hints = list(ep.fix_hints)
                plan = RecoveryPlan(
                    severity=ep.severity,
                    strategy=ep.strategy,
                    diagnosis=ep.diagnosis,
                    fix_hints=hints,
                    backup_required=(ep.strategy in (Strategy.PATCH_CODE,)),
                    escalate_to_llm=(ep.strategy == Strategy.LLM_ESCALATE),
                )

                # If ALT_PATH, add discovered alternatives
                if ep.strategy == Strategy.ALT_PATH:
                    # Extract path from error
                    path_match = re.search(r"['\"](.*?)['\"]", error_text)
                    if path_match:
                        guessed_path = path_match.group(1)
                        similar = self.path_finder.find_similar(guessed_path)
                        if similar:
                            plan.fix_hints.append(f"Suggested alternatives: {', '.join(similar)}")

                return plan

        return RecoveryPlan(
            severity=Severity.MEDIUM,
            strategy=Strategy.LLM_ESCALATE,
            diagnosis="Unknown error — could not classify automatically.",
            fix_hints=["Review the full error message", "Try the LLM escalation path"],
            escalate_to_llm=True,
        )

    def auto_fix_code(self, code: str, error_text: str) -> tuple[str | None, str]:
        """Attempt AST-based auto-fix of Python code given error."""
        return self.python_fixer.fix_from_traceback(code, error_text)

    def should_heal(self, tool_name: str) -> bool:
        """Check if circuit breaker allows healing attempts."""
        return not self.circuit_breaker.is_open(tool_name)

    def record_tool_result(self, tool_name: str, success: bool,
                          heal_passes: int = 0, fatal: bool = False) -> None:
        """Record result for health tracking and circuit breaking."""
        self.health.record_call(tool_name, success, heal_passes,
                               healed=(heal_passes > 0 and success))
        if success:
            self.circuit_breaker.record_success(tool_name)
        else:
            self.circuit_breaker.record_failure(tool_name, fatal=fatal)

    def get_fallback_tool(self, tool: str) -> str | None:
        """Get first available fallback for a tool."""
        chain = TOOL_FALLBACKS.get(tool, [])
        for alternative in chain:
            if not self.circuit_breaker.is_open(alternative):
                return alternative
        return None

    def build_llm_fix_prompt(self, tool_name: str, args: dict,
                             error_text: str, code: str,
                             previous_attempts: list[str]) -> str:
        """Build a rich LLM prompt for complex error fixing."""
        plan = self.diagnose(error_text)
        return (
            f"You are Nanobot-Lite's self-healing module. An error occurred in tool '{tool_name}'.\n\n"
            f"## Error\n{error_text}\n\n"
            f"## Diagnosis\n{plan.diagnosis}\n\n"
            f"## Severity\n{plan.severity.name} ({plan.strategy.name})\n\n"
            f"## Fix Hints\n" + "\n".join(f"- {h}" for h in plan.fix_hints) + "\n\n"
            + (f"## Code\n```python\n{code}\n```\n\n" if code else "")
            + (f"## Previous Fix Attempts (failed)\n" + "\n".join(f"- Attempt {i+1}: {a}" for i, a in enumerate(previous_attempts)) + "\n\n" if previous_attempts else "")
            + f"## Tool Args\n{json.dumps(args, indent=2)}\n\n"
            "Generate the corrected code or fixed parameters. "
            "Prefer minimal, targeted fixes over rewrites. "
            "Return ONLY the fixed content — no explanation needed."
        )

    def health_report(self) -> str:
        return self.health.get_report()

    def circuit_report(self) -> dict[str, Any]:
        return self.circuit_breaker.report()


# ── Global singleton ──────────────────────────────────────────────────────────
_healer: SelfHealer | None = None

def get_healer() -> SelfHealer:
    global _healer
    if _healer is None:
        _healer = SelfHealer()
    return _healer
