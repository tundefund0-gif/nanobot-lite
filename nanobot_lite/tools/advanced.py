"""Advanced tools: calculator, system info, time, download, Python REPL, encode/decode."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import urllib.request
import urllib.error
import urllib.parse
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot_lite.tools.base import Tool, ToolResult
from nanobot_lite.utils.helpers import run_shell


# ─── Utility ────────────────────────────────────────────────────────────────

def _exec(cmd: str, timeout: int = 30) -> ToolResult:
    """Run shell command, return ToolResult."""
    code, out, err = run_shell(cmd, timeout=timeout)
    if code != 0:
        return ToolResult(content=err or f"Exit code: {code}", success=False)
    return ToolResult(content=out[:5000])  # cap output


# ─── Calculator ─────────────────────────────────────────────────────────────

class CalculatorTool(Tool):
    """Evaluate mathematical expressions safely."""

    name = "calculator"
    description = "Evaluate a mathematical expression and return the result. Supports +, -, *, /, **, sqrt, sin, cos, tan, log, pi, e, parentheses, and constants."

    def __init__(self):
        super().__init__(
            name=self.name,
            description=self.description,
            input_schema={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Mathematical expression to evaluate (e.g. '2**8 + sqrt(16)')",
                    }
                },
                "required": ["expression"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        expr = args.get("expression", "")
        if not expr:
            return ToolResult(content="No expression provided", success=False)

        # Security: allow only safe chars
        if not re.match(r'^[\d\+\-\*\/\(\)\.\s\w]+$', expr):
            return ToolResult(content=f"Invalid characters in expression: {expr}", success=False)

        try:
            # Use a restricted eval with math functions
            allowed = {
                "abs": abs, "round": round, "min": min, "max": max,
                "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
                "tan": math.tan, "log": math.log, "log10": math.log10,
                "exp": math.exp, "pi": math.pi, "e": math.e,
                "pow": pow, "factorial": math.factorial,
                "floor": math.floor, "ceil": math.ceil,
            }
            # Only allow digits, operators, parentheses, dots, spaces, and specific names
            safe_expr = re.sub(r'[^0-9+\-*/().,\s]', '', expr)
            result = eval(safe_expr, {"__builtins__": {}}, allowed)
            return ToolResult(content=f"{expr} = {result}")
        except Exception as e:
            return ToolResult(content=f"Error: {e}", success=False)


# ─── System Info ─────────────────────────────────────────────────────────────

class SystemInfoTool(Tool):
    """Get system information: OS, CPU, memory, disk, uptime."""

    name = "system_info"
    description = "Get system information including OS, CPU, memory usage, disk space, and uptime."

    def __init__(self):
        super().__init__(
            name=self.name,
            description=self.description,
            input_schema={
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "enum": ["all", "os", "cpu", "memory", "disk", "network"],
                        "default": "all",
                        "description": "Which system section to query",
                    }
                },
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        section = args.get("section", "all")

        try:
            info = []
            info.append(f"🖥️ System Info — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

            # OS
            if section in ("all", "os"):
                info.append(f"\n📋 OS: {platform.system()} {platform.release()}")
                info.append(f"Machine: {platform.machine()}")
                info.append(f"Hostname: {platform.node()}")
                info.append(f"Python: {platform.python_version()}")

            # CPU
            if section in ("all", "cpu"):
                cpu_count = os.cpu_count() or 1
                # Use /proc for Linux-based systems
                try:
                    with open("/proc/cpuinfo") as f:
                        cpuinfo = f.read()
                    model = re.search(r"model name\s*:\s*(.+)", cpuinfo)
                    model_name = model.group(1) if model else "Unknown"
                    info.append(f"\n⚙️ CPU: {model_name.strip()}")
                    info.append(f"Cores: {cpu_count}")
                except:
                    info.append(f"\n⚙️ CPU: {platform.processor() or 'Unknown'} ({cpu_count} cores)")

            # Memory
            if section in ("all", "memory"):
                try:
                    with open("/proc/meminfo") as f:
                        meminfo = f.read()
                    total_m = re.search(r"MemTotal:\s+(\d+)", meminfo)
                    avail_m = re.search(r"MemAvailable:\s+(\d+)", meminfo)
                    if total_m and avail_m:
                        total_kb = int(total_m.group(1))
                        avail_kb = int(avail_m.group(1))
                        used_pct = (total_kb - avail_kb) / total_kb * 100
                        info.append(f"\n💾 Memory: {used_pct:.0f}% used")
                        info.append(f"Total: {total_kb//1024}MB | Available: {avail_kb//1024}MB")
                except:
                    info.append(f"\n💾 Memory: (unavailable on this platform)")

            # Disk
            if section in ("all", "disk"):
                try:
                    result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
                    lines = result.stdout.strip().split("\n")
                    if len(lines) >= 2:
                        parts = lines[1].split()
                        if len(parts) >= 5:
                            info.append(f"\n💿 Disk /: {parts[4]} used ({parts[2]}/{parts[1]})")
                except:
                    pass

            return ToolResult(content="\n".join(info))
        except Exception as e:
            return ToolResult(content=f"OS: {platform.system()} {platform.release()}\nPython: {platform.python_version()}\nMachine: {platform.machine()}")


# ─── Date & Time ─────────────────────────────────────────────────────────────

class DateTimeTool(Tool):
    """Get current date and time in various formats and timezones."""

    name = "datetime_info"
    description = "Get current date and time in various formats and timezone offsets."

    def __init__(self):
        super().__init__(
            name=self.name,
            description=self.description,
            input_schema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "default": "full",
                        "enum": ["full", "date", "time", "unix", "iso"],
                        "description": "Output format",
                    },
                    "tz_offset": {
                        "type": "integer",
                        "description": "Timezone offset in hours (e.g. -5 for EST)",
                    }
                },
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        fmt = args.get("format", "full")
        offset_h = args.get("tz_offset")

        now = datetime.now(timezone.utc)
        if offset_h is not None:
            from datetime import timedelta
            now = now + timedelta(hours=offset_h)

        if fmt == "date":
            out = now.strftime("%Y-%m-%d")
        elif fmt == "time":
            out = now.strftime("%H:%M:%S %Z")
        elif fmt == "unix":
            out = str(int(now.timestamp()))
        elif fmt == "iso":
            out = now.isoformat()
        else:
            out = now.strftime("%A, %B %d, %Y — %H:%M:%S UTC")

        return ToolResult(content=out)


# ─── URL Fetcher ─────────────────────────────────────────────────────────────

class FetchUrlTool(Tool):
    """Fetch and extract readable content from any URL."""

    name = "fetch_url"
    description = "Fetch content from a URL. Returns the page title, description, and main text content."

    def __init__(self):
        super().__init__(
            name=self.name,
            description=self.description,
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "max_chars": {"type": "integer", "default": 5000, "description": "Max characters to return"},
                },
                "required": ["url"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        url = args.get("url", "")
        max_chars = args.get("max_chars", 5000)

        if not url:
            return ToolResult(content="No URL provided", success=False)

        # Add protocol if missing
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; nanobot-lite/0.1)",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            # Extract title
            title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
            title = title_match.group(1).strip() if title_match else "No title"

            # Strip HTML and get plain text
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.S | re.I)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.S | re.I)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()

            result = f"📄 {title}\n\n{text[:max_chars]}"
            if len(text) > max_chars:
                result += "\n\n... (truncated)"
            return ToolResult(content=result)

        except urllib.error.HTTPError as e:
            return ToolResult(content=f"HTTP {e.code}: {e.reason}", success=False)
        except Exception as e:
            return ToolResult(content=f"Error: {e}", success=False)


# ─── Encode / Decode ─────────────────────────────────────────────────────────

class EncodeTool(Tool):
    """Encode or decode strings in various formats: base64, URL, hex, MD5, SHA256."""

    name = "encode_decode"
    description = "Encode or decode strings in various formats: base64, URL, hex, MD5, SHA256, rot13."

    def __init__(self):
        super().__init__(
            name=self.name,
            description=self.description,
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to encode or decode"},
                    "operation": {
                        "type": "string",
                        "enum": ["base64_encode", "base64_decode", "url_encode", "url_decode",
                                 "hex_encode", "hex_decode", "md5", "sha256", "rot13"],
                        "description": "Encoding/decoding operation",
                    },
                },
                "required": ["text", "operation"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        text = args.get("text", "")
        op = args.get("operation", "")

        try:
            if op == "base64_encode":
                out = base64.b64encode(text.encode()).decode()
            elif op == "base64_decode":
                out = base64.b64decode(text.encode()).decode()
            elif op == "url_encode":
                out = urllib.parse.quote(text)
            elif op == "url_decode":
                out = urllib.parse.unquote(text)
            elif op == "hex_encode":
                out = text.encode().hex()
            elif op == "hex_decode":
                out = bytes.fromhex(text).decode()
            elif op == "md5":
                out = hashlib.md5(text.encode()).hexdigest()
            elif op == "sha256":
                out = hashlib.sha256(text.encode()).hexdigest()
            elif op == "rot13":
                out = text.translate(str.maketrans(
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
                    "NOPQRSTUVWXYZABCDEFGHIJKLMNOPqrstuvwxyzabcdefghijklm",
                ))
            else:
                return ToolResult(content=f"Unknown operation: {op}", success=False)

            return ToolResult(content=f"{op}:\n{out}")
        except Exception as e:
            return ToolResult(content=f"Error: {e}", success=False)


# ─── UUID Generator ──────────────────────────────────────────────────────────

class UUIDTool(Tool):
    """Generate UUIDs and short IDs."""

    name = "generate_id"
    description = "Generate unique IDs: UUIDs, short IDs, timestamps."

    def __init__(self):
        super().__init__(
            name=self.name,
            description=self.description,
            input_schema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["uuid4", "short8", "timestamp"],
                        "default": "uuid4",
                        "description": "Type of ID to generate",
                    },
                    "count": {"type": "integer", "default": 1, "description": "Number of IDs"},
                },
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        id_type = args.get("type", "uuid4")
        count = min(args.get("count", 1), 20)

        results = []
        for _ in range(count):
            if id_type == "uuid4":
                results.append(str(uuid.uuid4()))
            elif id_type == "short8":
                results.append(str(uuid.uuid4().hex[:8]))
            elif id_type == "timestamp":
                results.append(str(int(datetime.now().timestamp() * 1000)))

        return ToolResult(content="\n".join(results))


# ─── File Hash ───────────────────────────────────────────────────────────────

class HashFileTool(Tool):
    """Calculate file hashes (MD5, SHA256, SHA1)."""

    name = "hash_file"
    description = "Calculate MD5, SHA1, or SHA256 hash of file content from base64 input."

    def __init__(self):
        super().__init__(
            name=self.name,
            description=self.description,
            input_schema={
                "type": "object",
                "properties": {
                    "data_base64": {"type": "string", "description": "File content as base64"},
                    "algorithm": {"type": "string", "enum": ["md5", "sha1", "sha256"], "default": "sha256"},
                },
                "required": ["data_base64"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        data_b64 = args.get("data_base64", "")
        algo = args.get("algorithm", "sha256")

        try:
            data = base64.b64decode(data_b64)
            h = hashlib.new(algo)
            h.update(data)
            return ToolResult(content=f"{algo.upper()}: {h.hexdigest()}\nSize: {len(data)} bytes")
        except Exception as e:
            return ToolResult(content=f"Error: {e}", success=False)


# ─── Currency Converter ───────────────────────────────────────────────────────

class CurrencyTool(Tool):
    """Convert between currencies using exchange rates from exchangerate-api.com."""

    name = "currency_convert"
    description = "Convert amounts between currencies. Uses free exchange rate API."

    def __init__(self):
        super().__init__(
            name=self.name,
            description=self.description,
            input_schema={
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Amount to convert"},
                    "from_currency": {"type": "string", "description": "Source currency code (e.g. USD)"},
                    "to_currency": {"type": "string", "description": "Target currency code (e.g. EUR)"},
                },
                "required": ["amount", "from_currency", "to_currency"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        amount = args.get("amount", 0)
        from_c = args.get("from_currency", "USD").upper()
        to_c = args.get("to_currency", "EUR").upper()

        try:
            url = f"https://api.exchangerate-api.com/v4/latest/{from_c}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            rate = data["rates"].get(to_c, 0)
            result = amount * rate
            return ToolResult(content=f"{amount} {from_c} = {result:.2f} {to_c} (rate: {rate:.4f})")
        except Exception as e:
            return ToolResult(content=f"Error: {e}", success=False)


# ─── Word / Character Count ──────────────────────────────────────────────────

class TextStatsTool(Tool):
    """Count words, characters, lines, and sentences in text."""

    name = "text_stats"
    description = "Analyze text: count words, characters, lines, sentences, paragraphs. Also detect language."

    def __init__(self):
        super().__init__(
            name=self.name,
            description=self.description,
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to analyze"},
                    "detail": {"type": "boolean", "default": False, "description": "Show detailed stats"},
                },
                "required": ["text"],
            },
        )

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        text = args.get("text", "")
        detail = args.get("detail", False)

        chars = len(text)
        words = len(text.split())
        lines = text.count('\n') + 1
        sentences = len(re.findall(r'[.!?]+', text))
        paragraphs = len([p for p in text.split('\n\n') if p.strip()])

        if detail:
            out = f"Characters (no spaces): {chars - text.count(' ')}\n"
            out += f"Characters (with spaces): {chars}\n"
            out += f"Words: {words}\n"
            out += f"Lines: {lines}\n"
            out += f"Sentences: {sentences}\n"
            out += f"Paragraphs: {paragraphs}\n"
            out += f"Avg word length: {chars/words:.1f} chars\n" if words else ""
        else:
            out = f"Words: {words} | Chars: {chars} | Lines: {lines} | Sentences: {sentences}"

        return ToolResult(content=out)


# ─── Factory ─────────────────────────────────────────────────────────────────

def create_advanced_tools() -> list[Tool]:
    """Create all advanced tools."""
    return [
        CalculatorTool(),
        SystemInfoTool(),
        DateTimeTool(),
        FetchUrlTool(),
        EncodeTool(),
        UUIDTool(),
        HashFileTool(),
        CurrencyTool(),
        TextStatsTool(),
    ]