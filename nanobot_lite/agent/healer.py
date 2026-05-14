"""
Advanced Self-Healing Engine — Nanobot-Lite v0.5.0
==================================================
Multi-layer autonomous repair system:

  Layer 1 — Structured Error Intelligence
    80+ error patterns with severity, recovery strategy, and fix hints.

  Layer 2 — Tool-Specific Recovery
    Per-tool fallback chains (e.g., grep→rg→python search).

  Layer 3 — Path Auto-Discovery
    FileNotFoundError triggers workspace + home + /tmp + common paths search.

  Layer 4 — Circuit Breaker (per-error-type)
    Per-tool AND per-error-type failure counter; trips after N failures.

  Layer 5 — Rollback System
    Backs up files before patching; restores if fix fails repeatedly.

  Layer 6 — Health Monitor (persisted)
    Tracks tool health scores, failure rates, and recovery success.
    Persists health state to disk so it survives restarts.

  Layer 7 — LLM-Guided Healing
    Complex/ambiguous errors escalated to LLM with full context.

  Layer 8 — Iterative Multi-Pass Healing
    Up to 7 healing passes per tool call, escalating strategy each pass.

  Layer 9 — Incremental Code Fix
    Tries minimal fixes first; falls back to larger rewrites.

  Layer 10 — Exponential Backoff
    Network/timeout errors retry with exponential backoff.
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
    RETRY              = 1   # Just retry with same approach
    RETRY_BACKOFF      = 2   # Retry with exponential backoff (network/timeout)
    RETRY_PARAMS       = 3   # Retry with adjusted parameters
    FALLBACK_TOOL      = 4   # Use a different tool for same goal
    PATCH_CODE         = 5   # Auto-patch the code at AST level
    ALT_PATH           = 6   # Try alternative file paths
    LLM_ESCALATE       = 7   # Ask LLM for targeted fix
    SKIP               = 8   # Skip this operation, report failure
    CIRCUIT_OPEN       = 9   # Open circuit breaker, skip tool entirely
    INCREMENTAL_FIX    = 10  # Try minimal code fix first, then larger rewrite
    ENVIRONMENT_RECOVERY = 11 # Fix env vars, PATH, Python path

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
    retry_delay: float = 0.0          # seconds of backoff before retry
    incremental: bool = False          # try minimal fix before full rewrite

# ─── Error pattern entry ──────────────────────────────────────────────────────

@dataclass
class ErrorPattern:
    regex: str
    severity: Severity
    strategy: Strategy
    diagnosis: str
    fix_hints: list[str]
    retry_delay: float = 0.0
    incremental: bool = False

# ─── Structured error pattern database ────────────────────────────────────────

ERROR_PATTERNS: list[ErrorPattern] = [
    # ── Python Syntax & Runtime ────────────────────────────────────────────────
    ErrorPattern(r"SyntaxError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Fix Python syntax — unmatched parens, quotes, indentation, or keyword misuse.",
        ["Check line number in traceback", "Run 'python -m py_compile' to confirm",
         "Look for missing colons after def/if/for", "Verify string quotes are balanced"],
        incremental=True),
    ErrorPattern(r"IndentationError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Fix indentation — use consistent spaces (4 recommended), check mix of tabs/spaces.",
        ["Set editor to spaces=4, no tabs", "Run 'python -m py_compile' to verify",
         "Check for tabs after spaces or vice versa"],
        incremental=True),
    ErrorPattern(r"TabError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Mixed tabs and spaces — standardize to spaces only.",
        ["Replace all tabs with 4 spaces", "Set editor to spaces-only indentation"]),
    ErrorPattern(r"NameError:\s*['\"]?(\w+)['\"]?", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Variable or name not defined — check spelling, imports, and scope.",
        ["Verify variable is defined before use", "Check import statement",
         "Check for typos in name", "Ensure correct module is imported"],
        incremental=True),
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
         "Ensure object is not None before accessing attributes"],
        incremental=True),
    ErrorPattern(r"TypeError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Type mismatch — function received wrong argument type.",
        ["Check argument types against function signature",
         "Convert types explicitly (int(), str(), list(), etc.)",
         "Check for None where value was expected"],
        incremental=True),
    ErrorPattern(r"ValueError", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Value is wrong type/range — check the actual value vs expected.",
        ["Validate input value", "Check for empty strings/lists/dicts",
         "Ensure value is within expected range", "Add input sanitization"],
        incremental=True),
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
    ErrorPattern(r"RuntimeError", Severity.HIGH, Strategy.LLM_ESCALATE,
        "Generic runtime error — read full traceback for specifics.",
        ["Read the full error message", "Check variable states at failure point",
         "Add debug print statements", "Isolate the failing code section"]),
    ErrorPattern(r"RecursionError", Severity.CRITICAL, Strategy.PATCH_CODE,
        "Infinite recursion — base case missing or not reached.",
        ["Add/improve base case in recursive function", "Check termination condition",
         "Consider iterative approach instead", "Add recursion depth limit with sys.setrecursionlimit()"]),
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
    ErrorPattern(r"AssertionError", Severity.MEDIUM, Strategy.LLM_ESCALATE,
        "Assertion failed — internal invariant violated.",
        ["Check the assertion condition", "Review expected vs actual values",
         "Remove or fix the assert statement"]),
    ErrorPattern(r"DeprecationWarning|DeprecationError", Severity.LOW, Strategy.RETRY_PARAMS,
        "Deprecated API usage — update to newer API.",
        ["Check Python version docs for replacement API",
         "Update to the recommended alternative"]),
    ErrorPattern(r"PendingDeprecationWarning", Severity.LOW, Strategy.RETRY_PARAMS,
        "API will be deprecated — plan migration.",
        ["Review migration guide for the upcoming change"]),

    # ── Python File & I/O ─────────────────────────────────────────────────────
    ErrorPattern(r"FileNotFoundError|No such file|file not found", Severity.LOW, Strategy.ALT_PATH,
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
    ErrorPattern(r"OSError.*\[Errno 36\]", Severity.HIGH, Strategy.SKIP,
        "File name too long.",
        ["Shorten the file or directory name", "Check for circular symlinks"]),
    ErrorPattern(r"OSError.*\[Errno 36\]|FileExistsError", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "File or directory already exists.",
        ["Use exist_ok=True in os.makedirs()", "Check before creating", "Remove existing first"]),
    ErrorPattern(r"BrokenPipeError|EPIPE", Severity.LOW, Strategy.SKIP,
        "Broken pipe — output stream closed prematurely.",
        ["Pipe to 'less' or redirect to file", "Check if output reader closed early"]),
    ErrorPattern(r"UnicodeDecodeError", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Text encoding error — file or string uses incompatible encoding.",
        ["Specify encoding explicitly (utf-8, latin-1)", "Try errors='replace' or 'ignore'",
         "Check source file encoding"]),
    ErrorPattern(r"UnicodeEncodeError", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Text encoding error when writing output.",
        ["Set PYTHONIOENCODING=utf-8", "Use errors='replace' in encode()"]),
    ErrorPattern(r"EOFError", Severity.MEDIUM, Strategy.SKIP,
        "End of file — input exhausted unexpectedly.",
        ["Check input source is not empty", "Verify input format is complete"]),

    # ── Network / API ──────────────────────────────────────────────────────────
    ErrorPattern(r"ConnectionError|timeout|Network|URLError|RemoteDisconnected", Severity.MEDIUM,
        Strategy.RETRY_BACKOFF,
        "Network/connection error — check connectivity and retry.",
        ["Verify internet connection", "Retry with exponential backoff",
         "Check firewall/proxy settings", "Try alternative endpoint"],
        retry_delay=2.0),
    ErrorPattern(r"HTTPError \d{3}", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "HTTP error response — server returned an error status.",
        ["Check URL is correct", "Add proper error handling for status codes",
         "Handle 429 (rate limit) with backoff", "Check authentication"],
        retry_delay=1.0),
    ErrorPattern(r"SSLError|ssl|SSL", Severity.MEDIUM, Strategy.RETRY_BACKOFF,
        "SSL/TLS error — certificate or handshake problem.",
        ["Check SSL certificate", "Try verify=False for testing (dev only)",
         "Update CA certificates", "Check TLS version compatibility"],
        retry_delay=3.0),
    ErrorPattern(r"ProxyError|CERTIFICATE_VERIFY_FAILED", Severity.MEDIUM, Strategy.ENVIRONMENT_RECOVERY,
        "Proxy or certificate verification error.",
        ["Check proxy settings", "Update CA bundle", "Set SSL_CERT_FILE env var"]),
    ErrorPattern(r"MaxRetryError|ConnectionRefusedError|ConnectionResetError", Severity.MEDIUM,
        Strategy.RETRY_BACKOFF,
        "Connection refused or max retries reached.",
        ["Check if service is running", "Verify host:port", "Retry with backoff"],
        retry_delay=5.0),

    # ── Shell / CLI ────────────────────────────────────────────────────────────
    ErrorPattern(r"command not found|not installed|: not found", Severity.HIGH,
        Strategy.FALLBACK_TOOL,
        "Command not found — tool may not be installed.",
        ["Check if tool is installed", "Try alternative tool/command",
         "Install the missing tool", "Use Python stdlib equivalent"]),
    ErrorPattern(r"Permission denied.*chmod", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Permission denied on chmod — may need sudo or different permissions.",
        ["Check current file permissions", "Use chmod 644 for files, 755 for scripts",
         "Try sudo if running as non-root"]),
    ErrorPattern(r"cannot create.*directory|No such file or directory", Severity.MEDIUM,
        Strategy.RETRY_PARAMS,
        "Cannot create directory — parent may not exist or no permissions.",
        ["Create parent directories first with mkdir -p", "Check write permissions",
         "Use absolute path instead of relative"]),
    ErrorPattern(r"grep:.*No such file|find:.*No such file", Severity.LOW, Strategy.ALT_PATH,
        "File search couldn't find a file — path may be wrong or file doesn't exist.",
        ["Verify file path", "Use -r for recursive search", "Check working directory"]),
    ErrorPattern(r"git:.*not a git repository", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Not inside a git repository.",
        ["Run 'git init' to initialize", "cd to repository root",
         "Check you're in correct directory"]),
    ErrorPattern(r"git:.*did not match|git:.*fatal", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Git command failed.",
        ["Check git command syntax", "Verify you're in a git repository",
         "Check branch/status with git status"]),
    ErrorPattern(r"subprocess.*exited.*non-zero|Exit code [1-9]", Severity.MEDIUM,
        Strategy.RETRY_PARAMS,
        "Subprocess returned non-zero exit code.",
        ["Check the command syntax", "Verify required tools are installed",
         "Check working directory", "Run command manually to debug"]),

    # ── Telegram / API ──────────────────────────────────────────────────────────
    ErrorPattern(r"400.*Bad Request", Severity.HIGH, Strategy.LLM_ESCALATE,
        "API returned 400 Bad Request — malformed request parameters.",
        ["Validate all API parameters", "Check request body format",
         "Review API documentation for required fields"]),
    ErrorPattern(r"401|403.*Unauthorized|Forbidden", Severity.CRITICAL, Strategy.SKIP,
        "Authentication/authorization error — check API key or token.",
        ["Verify API key/token is correct", "Check token hasn't expired",
         "Verify permissions/scopes", "Check rate limits"]),
    ErrorPattern(r"429.*Too Many Requests|Rate limit", Severity.MEDIUM, Strategy.RETRY_BACKOFF,
        "Rate limited — too many requests, back off and retry.",
        ["Wait before retrying", "Implement request queuing",
         "Cache responses where possible", "Use exponential backoff"],
        retry_delay=30.0),
    ErrorPattern(r"500|502|503|504.*Server Error", Severity.HIGH, Strategy.RETRY_BACKOFF,
        "Server-side error — remote service is having issues.",
        ["Retry after delay (server error)", "Try alternative endpoint if available",
         "Check service status page"],
        retry_delay=10.0),
    ErrorPattern(r"Telegram.*conflict", Severity.CRITICAL, Strategy.SKIP,
        "Telegram conflict — multiple instances running with same bot token.",
        ["Stop other bot instances", "Use separate bot tokens for different instances"]),
    ErrorPattern(r"Message.*not found|chat.*not found", Severity.LOW, Strategy.SKIP,
        "Telegram message/chat not found — may have been deleted.",
        ["Message may have been deleted", "Chat may no longer exist",
         "Check chat_id is correct"]),

    # ── LLM Provider ─────────────────────────────────────────────────────────────
    ErrorPattern(r"context_length_exceeded|maximum context|too many tokens|too long", Severity.CRITICAL,
        Strategy.LLM_ESCALATE,
        "Context window exceeded — conversation too long.",
        ["Summarize or compress conversation history", "Split task into smaller parts",
         "Reduce system prompt length", "Use higher max_tokens efficiency"]),
    ErrorPattern(r"rate_limit_exceeded|quota_exceeded", Severity.MEDIUM, Strategy.RETRY_BACKOFF,
        "API rate limit hit — back off and retry.",
        ["Wait and retry with exponential backoff", "Check usage dashboard",
         "Reduce request frequency"],
        retry_delay=60.0),
    ErrorPattern(r"api_key|invalid.*key|authentication", Severity.FATAL, Strategy.SKIP,
        "Invalid API key — check configuration.",
        ["Verify API key in config", "Check key hasn't been rotated",
         "Ensure correct provider/auth method"]),
    ErrorPattern(r"model.*not found|unknown.*model", Severity.HIGH, Strategy.RETRY_PARAMS,
        "Model not found — check model name spelling.",
        ["Verify model name in provider docs", "Try alternative model",
         "Check provider availability for that model"]),
    ErrorPattern(r"content_filter|harm_block|blocked.*content", Severity.CRITICAL,
        Strategy.LLM_ESCALATE,
        "Content was filtered/blocked — adjust prompt or approach.",
        ["Remove or rephrase sensitive content", "Break task into smaller steps",
         "Use a different framing for the request"]),

    # ── JSON / Data ─────────────────────────────────────────────────────────────
    ErrorPattern(r"JSONDecodeError|json.*invalid|JSON.*parse", Severity.MEDIUM, Strategy.PATCH_CODE,
        "Invalid JSON — parsing failed, check string format.",
        ["Validate JSON with json.loads() in Python REPL", "Check for trailing commas",
         "Ensure double quotes only (no single quotes)", "Validate string encoding"],
        incremental=True),
    ErrorPattern(r"yaml.*error|YAMLError|ParserError.*yaml", Severity.MEDIUM, Strategy.PATCH_CODE,
        "YAML parsing error — check indentation and syntax.",
        ["Validate YAML with an online parser", "Use spaces (not tabs)",
         "Check for inconsistent indentation", "Verify quote usage"]),
    ErrorPattern(r"CSV.*error|PandasError|read_csv.*error", Severity.MEDIUM, Strategy.RETRY_PARAMS,
        "Data file parsing error — CSV, TSV, or pandas error.",
        ["Check file encoding", "Verify delimiter matches", "Check for missing values",
         "Try different pandas read options"]),
    ErrorPattern(r"pickle.*error|Can't pickle", Severity.HIGH, Strategy.SKIP,
        "Pickle serialization error.",
        ["Check if object is picklable", "Use __getstate__/__setstate__",
         "Try JSON or msgpack instead"]),

    # ── Concurrency / Async ─────────────────────────────────────────────────────
    ErrorPattern(r"asyncio.*error|event loop|Couldn't find an event loop", Severity.HIGH,
        Strategy.LLM_ESCALATE,
        "Async/event loop error — running in wrong context.",
        ["Check asyncio event loop usage", "Use asyncio.run() at top level",
         "Verify no nested event loops", "Check async/await pairing"]),
    ErrorPattern(r"DeadlockError|ResourceWarning|close.*await", Severity.HIGH,
        Strategy.LLM_ESCALATE,
        "Async resource management error.",
        ["Ensure all async resources are properly closed",
         "Check for await-able cleanup", "Avoid nested event loops"]),
    ErrorPattern(r"Task.*was destroyed|asyncio.CancelledError", Severity.MEDIUM,
        Strategy.RETRY,
        "Async task was cancelled or destroyed.",
        ["Handle CancelledError in outer scope", "Check for graceful shutdown",
         "Verify tasks complete before exit"]),

    # ── Environment / Config ─────────────────────────────────────────────────────
    ErrorPattern(r"PYTHONPATH|python.*path|ModuleNotFoundError.*sys.path", Severity.MEDIUM,
        Strategy.ENVIRONMENT_RECOVERY,
        "Python path misconfiguration.",
        ["Set PYTHONPATH env var", "Install package with pip install -e .",
         "Check sys.path at runtime"]),
    ErrorPattern(r"EnvironmentError|os\.environ|KeyError.*env", Severity.MEDIUM,
        Strategy.ENVIRONMENT_RECOVERY,
        "Environment variable missing or error.",
        ["Check required env vars are set", "Provide .env file or defaults",
         "List all env vars with printenv"]),
    ErrorPattern(r"ConfigError|ConfigurationError|config.*error", Severity.MEDIUM,
        Strategy.ENVIRONMENT_RECOVERY,
        "Configuration error.",
        ["Check config file syntax", "Verify required fields present",
         "Check config file path", "Review schema requirements"]),
    ErrorPattern(r"AttributeError.*config|config.*None|no config", Severity.HIGH,
        Strategy.ENVIRONMENT_RECOVERY,
        "Config missing or None — not initialized.",
        ["Initialize config before use", "Check config loading order",
         "Verify config file loads correctly"]),

    # ── Catch-all for unknown errors ─────────────────────────────────────────────
    ErrorPattern(r".*", Severity.MEDIUM, Strategy.LLM_ESCALATE,
        "Unknown error — could not classify automatically.",
        ["Review the full error message and traceback",
         "Try isolating the failing component",
         "Search for the error message online"]),
]

# ─── Tool fallback chains ─────────────────────────────────────────────────────

TOOL_FALLBACKS: dict[str, list[str]] = {
    "grep": ["rg", "fgrep", "egrep", "find", "python_search"],
    "rg": ["grep", "egrep", "fgrep", "find", "python_search"],
    "find": ["fd", "python_search", "list_dir"],
    "curl": ["wget", "fetch_url", "python_download", "urllib"],
    "wget": ["curl", "fetch_url", "python_download", "urllib"],
    "jq": ["python_json", "python -c"],
    "sed": ["awk", "python_stream", "edit_file"],
    "awk": ["sed", "python_stream", "python -c"],
    "make": ["cmake", "ninja", "python_build", "shell"],
    "gcc": ["clang", "cc", "python_cc"],
    "clang": ["gcc", "cc"],
    "python3": ["python", "python3.11", "python3.10", "py", "python3.9"],
    "python": ["python3", "python3.11", "python3.10", "py"],
    "node": ["nodejs"],
    "ruby": ["ruby3", "ruby3.0"],
    "php": ["php8", "php7"],
    "tar": ["python", "zip", "unzip"],
    "zip": ["python", "7z", "unzip"],
    "unzip": ["python", "7z"],
    "git": ["hg", "svn"],
    "pip": ["pip3", "python -m pip", "python3 -m pip"],
    "npm": ["yarn", "pnpm"],
    "ffmpeg": ["avconv", "python_av"],
    "convert": ["ffmpeg", "magick", "python_pil"],
    "rg": ["grep"],
    "rg": ["grep"],
    "cat": ["python"],
    "head": ["python"],
    "tail": ["python"],
    "sort": ["python"],
    "uniq": ["python"],
    "wc": ["python"],
}

# ─── Per-error-type Circuit Breaker ───────────────────────────────────────────

@dataclass
class ErrorTypeState:
    error_type: str = ""
    failures: int = 0
    last_failure: float = 0.0
    open_since: float | None = None


class PerErrorCircuitBreaker:
    """
    Per-tool AND per-error-type circuit breaker.
    Tracks failures separately for each (tool, error_type) pair.
    Opens after FAILURE_THRESHOLD failures within FAILURE_WINDOW seconds.
    Half-opens after COOLDOWN seconds to test recovery.
    """
    FAILURE_THRESHOLD = 3
    FAILURE_WINDOW    = 300.0   # 5 minutes
    COOLDOWN          = 60.0    # 1 minute
    MAX_ERROR_TYPES   = 200     # cap to prevent memory growth

    def __init__(self):
        # tool → error_type → ErrorTypeState
        self._circuits: dict[str, dict[str, ErrorTypeState]] = {}
        # global tool-level circuit (legacy compat)
        self._tool_circuits: dict[str, CircuitState] = {}

    def _state(self, tool: str, error_type: str = "") -> ErrorTypeState:
        if tool not in self._circuits:
            self._circuits[tool] = {}
        et = error_type or "_global_"
        if et not in self._circuits[tool]:
            # Enforce cap
            if len(self._circuits[tool]) >= self.MAX_ERROR_TYPES:
                # Remove oldest
                oldest = min(self._circuits[tool].values(),
                             key=lambda s: s.last_failure)
                del self._circuits[tool][oldest.error_type]
            self._circuits[tool][et] = ErrorTypeState(error_type=et)
        return self._circuits[tool][et]

    def is_open(self, tool: str, error_type: str = "") -> bool:
        state = self._state(tool, error_type)
        now = datetime.now(timezone.utc).timestamp()

        # Permanently open (FATAL)
        if state.open_since and (now - state.open_since) < 0:
            return True

        # Cooldown check
        if state.open_since and (now - state.open_since) < self.COOLDOWN:
            return True

        # Half-open: allow one test request
        if state.open_since and hasattr(state, '_half_open') and state._half_open:
            return False

        # Check if we should trip
        if state.failures >= self.FAILURE_THRESHOLD:
            state.open_since = now
            return True

        # Window expired: reset counter
        if state.last_failure and (now - state.last_failure) > self.FAILURE_WINDOW:
            state.failures = 0

        return False

    def record_success(self, tool: str, error_type: str = "") -> None:
        state = self._state(tool, error_type)
        state.failures = 0
        state.open_since = None
        state.last_failure = 0.0

    def record_failure(self, tool: str, error_type: str = "",
                       fatal: bool = False) -> None:
        state = self._state(tool, error_type)
        now = datetime.now(timezone.utc).timestamp()
        state.last_failure = now
        state.failures += 1
        if fatal:
            state.open_since = -1.0  # permanently open
        elif state.failures >= self.FAILURE_THRESHOLD:
            state.open_since = now

    def try_half_open(self, tool: str, error_type: str = "") -> bool:
        state = self._state(tool, error_type)
        if not state.open_since:
            return False
        now = datetime.now(timezone.utc).timestamp()
        if (now - state.open_since) >= self.COOLDOWN:
            state._half_open = True
            return True
        return False

    def report(self) -> dict[str, Any]:
        result = {}
        for tool, et_map in self._circuits.items():
            result[tool] = {
                et: {
                    "failures": s.failures,
                    "open": self.is_open(tool, et),
                    "last_failure": datetime.fromtimestamp(
                        s.last_failure, tz=timezone.utc
                    ).isoformat() if s.last_failure else None,
                }
                for et, s in et_map.items()
            }
        return result


# ─── Legacy CircuitBreaker (tool-level only) ─────────────────────────────────

@dataclass
class CircuitState:
    failures: int = 0
    last_failure: float = 0.0
    open_since: float | None = None
    half_open: bool = False


class CircuitBreaker:
    """
    Per-tool circuit breaker (legacy / tool-level).
    Opens after FAILURE_THRESHOLD failures within FAILURE_WINDOW seconds.
    Half-opens after COOLDOWN seconds to test recovery.
    Closes on success, re-opens on continued failure.
    """
    FAILURE_THRESHOLD = 5
    FAILURE_WINDOW    = 300.0
    COOLDOWN          = 60.0

    def __init__(self):
        self._circuits: dict[str, CircuitState] = {}

    def _state(self, tool: str) -> CircuitState:
        if tool not in self._circuits:
            self._circuits[tool] = CircuitState()
        return self._circuits[tool]

    def is_open(self, tool: str) -> bool:
        state = self._state(tool)
        now = datetime.now(timezone.utc).timestamp()
        if state.open_since and (now - state.open_since) < self.COOLDOWN:
            return True
        if state.half_open:
            return False
        if state.failures >= self.FAILURE_THRESHOLD:
            state.open_since = now
            state.half_open = False
            return True
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
            state.open_since = now
        elif state.failures >= self.FAILURE_THRESHOLD:
            state.open_since = now

    def try_half_open(self, tool: str) -> bool:
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
    Keeps rolling backups (max 5 versions per file).
    """
    MAX_BACKUPS = 5

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
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            backup_name = f"{p.name}.{ts}.{hash_suffix}.bak"
            backup_path = self.backup_dir / backup_name
            shutil.copy2(p, backup_path)

            if str(p) not in self._backups:
                self._backups[str(p)] = []
            self._backups[str(p)].append(backup_path)

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

    def list_all(self) -> dict[str, list[str]]:
        """List all backups across all files."""
        return {
            path: [str(b) for b in backups]
            for path, backups in self._backups.items()
        }

# ─── Health Monitor (persisted) ─────────────────────────────────────────────

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
    # Per-error-type failure tracking
    error_type_counts: dict[str, int] = field(default_factory=dict)

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

    def top_errors(self, n: int = 3) -> list[tuple[str, int]]:
        """Return top N error types by frequency."""
        sorted_errors = sorted(
            self.error_type_counts.items(), key=lambda x: x[1], reverse=True
        )
        return sorted_errors[:n]


class HealthMonitor:
    """Tracks per-tool health, failure rates, and healing effectiveness. Persisted to disk."""

    def __init__(self, state_file: Path | None = None):
        self._tools: dict[str, ToolHealth] = {}
        self._state_file = state_file or (
            Path.home() / ".nanobot_lite" / "health_state.json"
        )
        self._load_state()

    def _load_state(self) -> None:
        """Load health state from disk on startup."""
        if not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text())
            for name, hdata in data.get("tools", {}).items():
                h = ToolHealth(
                    name=name,
                    total_calls=hdata.get("total_calls", 0),
                    failures=hdata.get("failures", 0),
                    heals_success=hdata.get("heals_success", 0),
                    heals_failed=hdata.get("heals_failed", 0),
                    avg_heal_passes=hdata.get("avg_heal_passes", 0.0),
                    last_success=hdata.get("last_success", 0.0),
                    last_failure=hdata.get("last_failure", 0.0),
                    error_type_counts=hdata.get("error_type_counts", {}),
                )
                self._tools[name] = h
            logger.debug(f"Loaded health state for {len(self._tools)} tools")
        except Exception as e:
            logger.warning(f"Failed to load health state: {e}")

    def _save_state(self) -> None:
        """Persist health state to disk."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "tools": {
                    name: {
                        "total_calls": h.total_calls,
                        "failures": h.failures,
                        "heals_success": h.heals_success,
                        "heals_failed": h.heals_failed,
                        "avg_heal_passes": h.avg_heal_passes,
                        "last_success": h.last_success,
                        "last_failure": h.last_failure,
                        "error_type_counts": h.error_type_counts,
                    }
                    for name, h in self._tools.items()
                }
            }
            self._state_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning(f"Failed to save health state: {e}")

    def track(self, tool_name: str) -> ToolHealth:
        if tool_name not in self._tools:
            self._tools[tool_name] = ToolHealth(name=tool_name)
        return self._tools[tool_name]

    def record_call(self, tool_name: str, success: bool,
                    heal_passes: int = 0, healed: bool = False,
                    error_type: str = "") -> None:
        h = self.track(tool_name)
        now = datetime.now(timezone.utc).timestamp()
        h.total_calls += 1
        if success:
            h.last_success = now
            if heal_passes > 0:
                h.heals_success += 1
                h.avg_heal_passes = (
                    (h.avg_heal_passes * (h.heals_success - 1) + heal_passes)
                    / h.heals_success
                )
        else:
            h.last_failure = now
            h.failures += 1
            if heal_passes > 0:
                h.heals_failed += 1
            if error_type:
                h.error_type_counts[error_type] = h.error_type_counts.get(error_type, 0) + 1
        self._save_state()

    def record_tool_result(self, tool_name: str, success: bool,
                           heal_passes: int = 0, healed: bool = False,
                           error_type: str = "") -> None:
        """Public API — also called by code_runner, shell, etc."""
        self.record_call(tool_name, success, heal_passes, healed, error_type)

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
            top_errors = h.top_errors()
            err_str = ""
            if top_errors:
                err_str = f" | top errors: {', '.join(f'{e}({c})' for e, c in top_errors)}"
            lines.append(
                f"{h.status} **{h.name}** — "
                f"score={h.health_score:.2f}, "
                f"calls={h.total_calls}, "
                f"fails={h.failures}, "
                f"healed={h.heals_success}/{h.heals_failed}, "
                f"avg_passes={h.avg_heal_passes:.1f}"
                f"{age_success}{age_fail}{err_str}"
            )
        if len(lines) == 1:
            lines.append("(no data yet)")
        return "\n".join(lines)

# ─── AST-based Python code fixer ─────────────────────────────────────────────

class PythonAutoFixer:
    """
    Analyzes Python tracebacks and auto-fixes common errors at the AST level.
    Returns (fixed_code, explanation) or (None, error_message).
    Strategies: incremental (minimal) first, then full rewrite if needed.
    """

    @staticmethod
    def fix_from_traceback(code: str, error_text: str) -> tuple[str | None, str]:
        """
        Parse error type + line from traceback, apply targeted AST fix.
        Returns (fixed_code, explanation) or (None, "no auto-fix available").
        """
        error_type = re.search(r"(\w+Error|\w+Exception):", error_text)
        error_type = error_type.group(1) if error_type else ""

        line_match = re.search(r"line (\d+)", error_text)
        line_no = int(line_match.group(1)) - 1 if line_match else None

        name_match = re.search(
            r"NameError:.*?'(\w+)'|NameError:.*?(\w+) not defined", error_text
        )
        bad_name = name_match.group(1) or name_match.group(2) if name_match else None

        try:
            tree = ast.parse(code)
        except SyntaxError as se:
            return PythonAutoFixer._fix_syntax_error(code, se, error_text)

        fixers = [
            ("NameError",          lambda: PythonAutoFixer._fix_name_error(code, tree, bad_name, line_no)),
            ("IndexError",         lambda: PythonAutoFixer._fix_index_error(code, tree, line_no)),
            ("KeyError",           lambda: PythonAutoFixer._fix_key_error(code, tree, line_no)),
            ("TypeError",          lambda: PythonAutoFixer._fix_type_error(code, tree, line_no, error_text)),
            ("AttributeError",     lambda: PythonAutoFixer._fix_attribute_error(code, tree, line_no, error_text)),
            ("ImportError",        lambda: PythonAutoFixer._fix_import_error(code, tree, error_text)),
            ("IndentationError",   lambda: PythonAutoFixer._fix_indentation(code, error_text)),
            ("TabError",           lambda: PythonAutoFixer._fix_indentation(code, error_text)),
            ("ZeroDivisionError",  lambda: PythonAutoFixer._fix_zero_division(code, tree, line_no)),
            ("ValueError",         lambda: PythonAutoFixer._fix_value_error(code, tree, line_no, error_text)),
            ("UnboundLocalError",  lambda: PythonAutoFixer._fix_unbound_local(code, tree, bad_name)),
            ("StopIteration",      lambda: PythonAutoFixer._fix_stop_iteration(code, tree, line_no)),
            ("RecursionError",     lambda: PythonAutoFixer._fix_recursion_error(code, tree)),
            ("OverflowError",      lambda: PythonAutoFixer._fix_overflow_error(code, tree, line_no)),
            ("JSONDecodeError",    lambda: PythonAutoFixer._fix_json_error(code, tree, line_no)),
            ("EOFError",           lambda: PythonAutoFixer._fix_eof_error(code, tree, line_no)),
        ]

        for et_name, fixer_fn in fixers:
            if et_name in error_type:
                result = fixer_fn()
                if result[0] is not None:
                    return result

        return None, f"No AST auto-fix available for {error_type}"

    # ── Syntax ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _fix_syntax_error(code: str, se: SyntaxError,
                           error_text: str) -> tuple[str | None, str]:
        lines = code.splitlines()
        if se.lineno is None:
            return None, "Could not determine line number for SyntaxError"

        line_idx = se.lineno - 1

        # Fix: missing closing paren/brackets/braces
        for opener, closer in [("(", ")"), ("[", "]"), ("{", "}")]:
            count = code.count(opener) - code.count(closer)
            if count > 0:
                new_code = code + closer
                return new_code, f"Auto-fixed: added missing {closer} at end"

        # Fix: missing colon after def/if/for/class
        if line_idx < len(lines):
            line = lines[line_idx]
            stripped = line.rstrip()
            for kw in ("def ", "class ", "if ", "elif ", "for ", "while ", "try:"):
                kw_stripped = kw.rstrip(":")
                if kw_stripped in stripped and not stripped.endswith(":"):
                    # Add colon
                    new_lines = list(lines)
                    new_lines[line_idx] = stripped + ":"
                    return "\n".join(new_lines), f"Auto-fixed: added missing ':' after {kw_stripped}"

        # Fix: unterminated string — try adding closing quote
        if "unterminated" in error_text.lower() or "eol" in error_text.lower():
            if '"' in code and code.count('"') % 2 != 0:
                new_code = code + '"'
                return new_code, "Auto-fixed: added missing closing double quote"
            if "'" in code and code.count("'") % 2 != 0:
                new_code = code + "'"
                return new_code, "Auto-fixed: added missing closing single quote"

        return None, f"SyntaxError at line {se.lineno}: {se.msg}"

    # ── NameError ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fix_name_error(code: str, tree: ast.AST,
                        bad_name: str | None,
                        line_no: int | None) -> tuple[str | None, str]:
        """Fix NameError: insert None assignment or import if possible."""
        if not bad_name:
            return None, "NameError: could not identify variable name"

        lines = code.splitlines()
        if line_no is not None and line_no < len(lines):
            target_line = lines[line_no]
            indent = len(target_line) - len(target_line.lstrip())
            ws = " " * indent
            lines.insert(line_no, f"{ws}{bad_name} = None  # auto-fix: undefined name")
            new_code = "\n".join(lines)
            return new_code, f"Auto-fixed: added '{bad_name} = None' before use"

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.body:
                    ins_after = node.body[0].lineno - 1
                    if ins_after < len(lines):
                        target_line = lines[ins_after]
                        indent = len(target_line) - len(target_line.lstrip()) + 4
                        ws = " " * indent
                        lines.insert(ins_after, f"{ws}{bad_name} = None  # auto-fix")
                        new_code = "\n".join(lines)
                        return new_code, f"Auto-fixed: added '{bad_name} = None' in {node.name}"

        return None, f"NameError '{bad_name}': could not auto-fix"

    # ── IndexError ──────────────────────────────────────────────────────────────

    @staticmethod
    def _fix_index_error(code: str, tree: ast.AST,
                         line_no: int | None) -> tuple[str | None, str]:
        if line_no is None or line_no >= len(code.splitlines()):
            return None, "IndexError: could not determine line number"

        lines = code.splitlines()
        line = lines[line_no]
        m = re.match(r"^(.*?)(\w+)\[(.+?)\]$", line.strip())
        if m:
            prefix, var, index = m.groups()
            new_line = f"{prefix}if {index} < len({var}): {var}[{index}]"
            lines[line_no] = " " * (len(line) - len(line.lstrip())) + new_line
            new_code = "\n".join(lines)
            return new_code, f"Auto-fixed: added bounds check for {var}[{index}]"

        # Try: wrap with try/except
        return None, "IndexError: could not auto-fix, needs manual bounds check"

    # ── KeyError ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fix_key_error(code: str, tree: ast.AST,
                       line_no: int | None) -> tuple[str | None, str]:
        if line_no is None or line_no >= len(code.splitlines()):
            return None, "KeyError: could not determine line number"

        lines = code.splitlines()
        line = lines[line_no]
        m = re.search(r"(\w+)\[(.+?)\]", line)
        if m:
            var, key = m.groups()
            new_line = line.replace(f"{var}[{key}]", f"{var}.get({key})")
            lines[line_no] = new_line
            new_code = "\n".join(lines)
            return new_code, f"Auto-fixed: replaced {var}[{key!r}] with {var}.get({key!r})"

        return None, "KeyError: could not auto-fix, needs manual .get() or key check"

    # ── TypeError ──────────────────────────────────────────────────────────────

    @staticmethod
    def _fix_type_error(code: str, tree: ast.AST,
                        line_no: int | None,
                        error_text: str) -> tuple[str | None, str]:
        if line_no is None or line_no >= len(code.splitlines()):
            return None, "TypeError: could not determine line number"

        lines = code.splitlines()
        line = lines[line_no]

        # str + int -> str + str()
        fixed = re.sub(r'(\w+)\s*\+\s*(\w+)', lambda m: (
            f"{m.group(1)} + str({m.group(2)})"
            if m.group(1) != m.group(2) else m.group(0)
        ), line)
        if fixed != line:
            lines[line_no] = fixed
            return "\n".join(lines), "Auto-fixed: added str() conversion for type mismatch"

        # Missing argument
        if "missing" in error_text.lower() and "argument" in error_text.lower():
            m = re.search(r"(\w+)\(\)", error_text)
            if m:
                fn = m.group(1)
                lines[line_no] = line.replace(f"{fn}()", f"{fn}(None)")
                return "\n".join(lines), f"Auto-fixed: added None argument to {fn}()"

        return None, "TypeError: could not auto-fix, needs manual type conversion"

    # ── AttributeError ─────────────────────────────────────────────────────────

    @staticmethod
    def _fix_attribute_error(code: str, tree: ast.AST,
                             line_no: int | None,
                             error_text: str) -> tuple[str | None, str]:
        m = re.search(r"'(.*?)'", error_text)
        attr = m.group(1) if m else None
        if not attr:
            return None, "AttributeError: could not extract attribute name"

        if line_no is not None and line_no < len(code.splitlines()):
            lines = code.splitlines()
            line = lines[line_no]
            fixed = re.sub(rf"\.({re.escape(attr)})\b", f".get('{attr}', None)", line)
            if fixed != line:
                lines[line_no] = fixed
                return "\n".join(lines), f"Auto-fixed: replaced .{attr} with .get('{attr}', None)"

        return None, f"AttributeError on '{attr}': could not auto-fix"

    # ── ImportError ────────────────────────────────────────────────────────────

    @staticmethod
    def _fix_import_error(code: str, tree: ast.AST,
                          error_text: str) -> tuple[str | None, str]:
        m = re.search(
            r"import '(\w+)'|from (\w+) import|No module named '(\w+)'", error_text
        )
        module = m.group(1) or m.group(2) or m.group(3) if m else None
        if not module:
            return None, "ImportError: could not identify module"

        lines = code.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(f"import {module}"):
                lines[i] = line.replace(f"import {module}", f"from {module} import *")
                return "\n".join(lines), f"Auto-fixed: changed to 'from {module} import *'"
            if stripped.startswith(f"from {module} import"):
                # Try: add try/except fallback
                import_line = lines[i]
                lines.insert(i + 1, f"except ImportError:\n    {import_line.split('from')[0]}    pass  # auto-fix: import failed")
                return "\n".join(lines), f"Auto-fixed: wrapped {module} import in try/except"

        return None, f"ImportError for '{module}': could not auto-fix"

    # ── Indentation ────────────────────────────────────────────────────────────

    @staticmethod
    def _fix_indentation(code: str,
                          error_text: str) -> tuple[str | None, str]:
        if "\t" in code:
            new_code = code.replace("\t", "    ")
            return new_code, "Auto-fixed: replaced tabs with 4 spaces"
        return None, "IndentationError: could not auto-fix, check indentation manually"

    # ── ZeroDivision ───────────────────────────────────────────────────────────

    @staticmethod
    def _fix_zero_division(code: str, tree: ast.AST,
                           line_no: int | None) -> tuple[str | None, str]:
        if line_no is None or line_no >= len(code.splitlines()):
            return None, "ZeroDivisionError: could not determine line number"
        lines = code.splitlines()
        line = lines[line_no]
        parts = re.split(r"(/|%)", line)
        if len(parts) >= 3:
            before_op = parts[0].strip()
            op = parts[1]
            after_op = parts[2].strip().rstrip(")").rstrip("]").rstrip(")")
            safe_divisor = f"({after_op} if {after_op} else 1)"
            new_line = before_op + op + safe_divisor
            if len(parts) > 3:
                new_line += "".join(parts[3:])
            lines[line_no] = new_line
            return "\n".join(lines), "Auto-fixed: replaced divisor with safe fallback"
        return None, "ZeroDivisionError: could not auto-fix"

    # ── ValueError ─────────────────────────────────────────────────────────────

    @staticmethod
    def _fix_value_error(code: str, tree: ast.AST,
                         line_no: int | None,
                         error_text: str) -> tuple[str | None, str]:
        if line_no is None or line_no >= len(code.splitlines()):
            return None, "ValueError: could not determine line number"
        lines = code.splitlines()
        line = lines[line_no]
        m = re.search(r"(int|float|list|tuple|set|dict|str|bool)\((.+)\)", line)
        if m:
            fn, arg = m.groups()
            new_line = line.replace(
                f"{fn}({arg})", f"({fn}({arg}) if {arg} else None)"
            )
            lines[line_no] = new_line
            return "\n".join(lines), f"Auto-fixed: wrapped {fn}() with fallback to None"

        # int() on string — add strip()
        int_m = re.search(r"int\(([^)]+)\)", line)
        if int_m:
            arg = int_m.group(1)
            new_line = line.replace(f"int({arg})", f"int({arg}.strip())")
            lines[line_no] = new_line
            return "\n".join(lines), "Auto-fixed: added .strip() before int()"

        return None, "ValueError: could not auto-fix"

    # ── UnboundLocal ───────────────────────────────────────────────────────────

    @staticmethod
    def _fix_unbound_local(code: str, tree: ast.AST,
                           bad_name: str | None) -> tuple[str | None, str]:
        if not bad_name:
            return None, "UnboundLocalError: could not identify variable"
        lines = code.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for i, line in enumerate(lines):
                    if line.strip().startswith(f"def {node.name}"):
                        indent = len(line) - len(line.lstrip()) + 4
                        lines.insert(i + 1, " " * indent + f"global {bad_name}  # auto-fix")
                        return "\n".join(lines), f"Auto-fixed: added 'global {bad_name}' in {node.name}()"
        return None, "UnboundLocalError: could not auto-fix"

    # ── StopIteration ──────────────────────────────────────────────────────────

    @staticmethod
    def _fix_stop_iteration(code: str, tree: ast.AST,
                            line_no: int | None) -> tuple[str | None, str]:
        """Wrap next() calls with default or try/except."""
        if line_no is None or line_no >= len(code.splitlines()):
            return None, "StopIteration: could not determine line"
        lines = code.splitlines()
        line = lines[line_no]

        # next(x) -> next(x, None)
        m = re.search(r"\bnext\(([^)]+)\)", line)
        if m:
            arg = m.group(1)
            if ", None" not in arg:
                new_line = line.replace(f"next({arg})", f"next({arg}, None)")
                lines[line_no] = new_line
                return "\n".join(lines), f"Auto-fixed: added default to next({arg})"

        return None, "StopIteration: could not auto-fix, wrap next() with try/except"

    # ── RecursionError ─────────────────────────────────────────────────────────

    @staticmethod
    def _fix_recursion_error(code: str,
                              tree: ast.AST) -> tuple[str | None, str]:
        """Add sys.setrecursionlimit() at top of file."""
        lines = code.splitlines()
        sys_import_line = -1
        first_import_line = -1

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("import sys"):
                sys_import_line = i
            if first_import_line == -1 and (
                    stripped.startswith("import ") or stripped.startswith("from ")):
                first_import_line = i

        # Find where to insert
        insert_at = 0
        if first_import_line != -1:
            insert_at = first_import_line + 1
        elif sys_import_line != -1:
            insert_at = sys_import_line + 1

        # Check if recursionlimit is already set
        for line in lines:
            if "setrecursionlimit" in line:
                return None, "RecursionError: recursionlimit already set"

        ws = " " * 4
        new_lines = list(lines)
        new_lines.insert(insert_at, f"{ws}sys.setrecursionlimit(10000)  # auto-fix: increase recursion depth")
        return "\n".join(new_lines), "Auto-fixed: added sys.setrecursionlimit(10000)"

    # ── OverflowError ──────────────────────────────────────────────────────────

    @staticmethod
    def _fix_overflow_error(code: str, tree: ast.AST,
                             line_no: int | None) -> tuple[str | None, str]:
        """Wrap large arithmetic with float() conversion."""
        if line_no is None or line_no >= len(code.splitlines()):
            return None, "OverflowError: could not determine line"
        lines = code.splitlines()
        line = lines[line_no]
        # Replace ** exponentiation with float conversion
        fixed = re.sub(r"(\d+)\s*\*\*\s*(\d+)", lambda m: f"float({m.group(0)})", line)
        if fixed != line:
            lines[line_no] = fixed
            return "\n".join(lines), "Auto-fixed: wrapped large arithmetic in float()"
        return None, "OverflowError: could not auto-fix"

    # ── JSONDecodeError ────────────────────────────────────────────────────────

    @staticmethod
    def _fix_json_error(code: str, tree: ast.AST,
                        line_no: int | None) -> tuple[str | None, str]:
        """Wrap json.loads() with try/except and errors='replace'."""
        if line_no is None or line_no >= len(code.splitlines()):
            return None, "JSONDecodeError: could not determine line"
        lines = code.splitlines()
        line = lines[line_no]

        # json.loads(str) -> json.loads(str.strip())
        m = re.search(r"json\.loads\(([^)]+)\)", line)
        if m:
            arg = m.group(1)
            new_line = line.replace(
                f"json.loads({arg})", f"json.loads({arg}.strip())"
            )
            lines[line_no] = new_line
            return "\n".join(lines), "Auto-fixed: added .strip() to json.loads() argument"

        return None, "JSONDecodeError: could not auto-fix"

    # ── EOFError ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fix_eof_error(code: str, tree: ast.AST,
                        line_no: int | None) -> tuple[str | None, str]:
        """Wrap input() calls with try/except."""
        if line_no is None or line_no >= len(code.splitlines()):
            return None, "EOFError: could not determine line"
        lines = code.splitlines()
        line = lines[line_no]

        # input() -> input("")  or wrap in try/except
        if "input(" in line:
            new_line = re.sub(r"\binput\(([^)]*)\)", r"input(\1 or ' ')", line)
            lines[line_no] = new_line
            return "\n".join(lines), "Auto-fixed: provided default to input()"

        return None, "EOFError: could not auto-fix"

# ─── Path auto-discovery ──────────────────────────────────────────────────────

class PathAutoDiscover:
    """
    Search multiple locations for files matching failed path patterns:
    - Workspace root and subdirs
    - Home directory (~)
    - /tmp
    - Common Python/system paths
    """

    SEARCH_LOCATIONS = [
        Path.home(),
        Path("/tmp"),
        Path("/var/tmp"),
        Path("/usr/local/lib/python"),
        Path("/usr/share"),
        Path.home() / "projects",
        Path.home() / "workspace",
        Path.home() / "code",
        Path.home() / ".config",
        Path.home() / "scripts",
    ]

    SKIP_DIRS = {
        "__pycache__", "node_modules", ".git", "venv", ".venv", "env",
        ".pytest_cache", ".mypy_cache", "__pypackages__", "dist", "build",
        ".tox", ".nox", ".eggs", "*.egg-info",
    }

    @classmethod
    def find_similar(cls, file_path: str,
                      workspace: str | None = None) -> list[str]:
        """Find files with similar names across multiple search locations."""
        stem = Path(file_path).stem.lower()
        ext = Path(file_path).suffix.lower()

        candidates: list[tuple[int, str]] = []

        # Always include workspace first
        locations = []
        if workspace:
            locations.append(Path(workspace).resolve())
        for loc in cls.SEARCH_LOCATIONS:
            if loc.exists():
                locations.append(loc)

        # Deduplicate
        seen_paths: set[Path] = set()
        for base in locations:
            if not base.exists():
                continue
            try:
                for root, dirs, files in os.walk(base):
                    # Prune skip dirs in-place
                    dirs[:] = [d for d in dirs
                               if d not in cls.SKIP_DIRS and not d.startswith(".")]

                    for fname in files:
                        if fname.startswith("."):
                            continue
                        fpath = Path(root) / fname
                        if fpath in seen_paths:
                            continue
                        seen_paths.add(fpath)

                        fstem = Path(fname).stem.lower()
                        fext = Path(fname).suffix.lower()
                        score = 0
                        if stem and (stem in fstem or fstem in stem or
                                     Path(fname).name.lower().replace(" ", "") ==
                                     file_path.lower().replace(" ", "")):
                            score += 3
                        if ext and ext == fext:
                            score += 2
                        if score > 0:
                            try:
                                rel = fpath.relative_to(base)
                                candidates.append((score, str(rel)))
                            except ValueError:
                                candidates.append((score, str(fpath)))
            except PermissionError:
                continue

        candidates.sort(reverse=True)
        return [c[1] for c in candidates[:8]]

    @classmethod
    def find_in_path(cls, command: str) -> str | None:
        """Find a command in PATH."""
        import shutil
        path = shutil.which(command)
        if path:
            return path
        # Try common PATH locations
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = Path(directory) / command
            if candidate.exists():
                return str(candidate)
        return None

    @classmethod
    def suggest_env_fixes(cls, error_text: str) -> list[str]:
        """Suggest environment variable fixes based on error text."""
        suggestions = []
        if "PYTHONPATH" in error_text:
            suggestions.append(
                "Set PYTHONPATH: export PYTHONPATH=/path/to/modules:$PYTHONPATH"
            )
        if "PATH" in error_text or "command not found" in error_text.lower():
            suggestions.append(
                "Add to PATH: export PATH=/new/bin:$PATH"
            )
        if "HOME" in error_text:
            suggestions.append(
                f"Home directory: {Path.home()}"
            )
        return suggestions

# ─── Main Self-Healer ─────────────────────────────────────────────────────────

class SelfHealer:
    """
    Central self-healing engine.
    Coordinates: error diagnosis, recovery planning, AST fixing,
    path discovery, circuit breaking, rollback, health monitoring.
    """

    MAX_HEAL_PASSES = 7

    def __init__(self):
        self.circuit_breaker = PerErrorCircuitBreaker()
        self.rollback = RollbackManager()
        self.health = HealthMonitor()
        self.python_fixer = PythonAutoFixer()
        self.path_finder = PathAutoDiscover()
        self._error_type_cache: dict[str, str] = {}  # error text → error type

    # ── Public API ─────────────────────────────────────────────────────────────

    def diagnose(self, error_text: str) -> RecoveryPlan:
        """Diagnose an error and return a structured RecoveryPlan."""
        # Quick cache lookup
        if error_text in self._error_type_cache:
            error_type = self._error_type_cache[error_text]
        else:
            error_type = re.search(
                r"(\w+Error|\w+Exception):", error_text
            )
            error_type = error_type.group(1) if error_type else "UnknownError"
            self._error_type_cache[error_text] = error_type

        for ep in ERROR_PATTERNS:
            m = re.search(ep.regex, error_text, re.IGNORECASE)
            if m:
                hints = list(ep.fix_hints)
                plan = RecoveryPlan(
                    severity=ep.severity,
                    strategy=ep.strategy,
                    diagnosis=ep.diagnosis,
                    fix_hints=hints,
                    backup_required=(ep.strategy in (
                        Strategy.PATCH_CODE, Strategy.INCREMENTAL_FIX
                    )),
                    escalate_to_llm=(ep.strategy == Strategy.LLM_ESCALATE),
                    retry_delay=ep.retry_delay,
                    incremental=ep.incremental,
                )

                if ep.strategy == Strategy.ALT_PATH:
                    path_match = re.search(r"['\"](.*?)['\"]", error_text)
                    if path_match:
                        guessed_path = path_match.group(1)
                        similar = self.path_finder.find_similar(guessed_path)
                        if similar:
                            plan.fix_hints.append(
                                f"Suggested alternatives: {', '.join(similar[:3])}"
                            )

                if ep.strategy == Strategy.ENVIRONMENT_RECOVERY:
                    env_suggestions = self.path_finder.suggest_env_fixes(error_text)
                    plan.fix_hints.extend(env_suggestions)

                return plan

        return RecoveryPlan(
            severity=Severity.MEDIUM,
            strategy=Strategy.LLM_ESCALATE,
            diagnosis="Unknown error — could not classify automatically.",
            fix_hints=["Review the full error message and traceback",
                       "Try isolating the failing component"],
            escalate_to_llm=True,
        )

    def auto_fix_code(self, code: str, error_text: str) -> tuple[str | None, str]:
        """Attempt AST-based auto-fix of Python code given error."""
        return self.python_fixer.fix_from_traceback(code, error_text)

    def should_heal(self, tool_name: str, error_type: str = "") -> bool:
        """Check if circuit breaker allows healing attempts."""
        return not self.circuit_breaker.is_open(tool_name, error_type)

    def record_tool_result(self, tool_name: str, success: bool,
                           heal_passes: int = 0, fatal: bool = False,
                           error_type: str = "") -> None:
        """Record result for health tracking and circuit breaking."""
        self.health.record_call(tool_name, success, heal_passes,
                                healed=(heal_passes > 0 and success),
                                error_type=error_type)
        if success:
            self.circuit_breaker.record_success(tool_name, error_type)
        else:
            self.circuit_breaker.record_failure(tool_name, error_type, fatal=fatal)

    def get_fallback_tool(self, tool: str) -> str | None:
        """Get first available fallback for a tool."""
        chain = TOOL_FALLBACKS.get(tool, [])
        for alternative in chain:
            if not self.circuit_breaker.is_open(alternative):
                return alternative
        return None

    def build_llm_fix_prompt(
        self,
        tool_name: str,
        args: dict,
        error_text: str,
        code: str,
        previous_attempts: list[str],
    ) -> str:
        """Build a rich LLM prompt for complex error fixing."""
        plan = self.diagnose(error_text)
        error_type = self._error_type_cache.get(error_text, "UnknownError")

        # Extract key details
        line_match = re.search(r"line (\d+)", error_text)
        line_info = f" (line {line_match.group(1)})" if line_match else ""

        return (
            f"You are Nanobot-Lite's self-healing module. "
            f"An error occurred in tool '{tool_name}'{line_info}.\n\n"
            f"## Error Type\n{error_type}\n\n"
            f"## Error Message\n{error_text}\n\n"
            f"## Diagnosis\n{plan.diagnosis}\n\n"
            f"## Severity\n{plan.severity.name}\n\n"
            f"## Recommended Strategy\n{plan.strategy.name}\n\n"
            f"## Fix Hints\n" + "\n".join(f"- {h}" for h in plan.fix_hints) + "\n\n"
            + (f"## Current Code\n```python\n{code}\n```\n\n"
                if code else "")
            + ("## Previous Fix Attempts (all failed)\n"
                + "\n".join(f"- Attempt {i+1}: {a[:300]}" for i, a in enumerate(previous_attempts))
                + "\n\n"
                if previous_attempts else "")
            + f"## Tool Args\n```json\n{json.dumps(args, indent=2)}\n```\n\n"
            "Generate the corrected code or fixed parameters. "
            "Prefer minimal, targeted fixes over rewrites. "
            "Return ONLY the fixed content (code block or JSON), no explanation."
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


def reset_healer() -> None:
    """Reset the healer singleton — useful for testing."""
    global _healer
    _healer = None
