"""Helper utilities for the agent."""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Estimate tokens (rough: ~4 chars per token for English)
def estimate_tokens(text: str) -> int:
    """Rough token estimation (~4 chars per token)."""
    return max(1, len(text) // 4)


def estimate_message_tokens(msg: dict[str, Any]) -> int:
    """Estimate tokens in a message dict."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return estimate_tokens(content)
    # For content blocks, estimate each
    total = 0
    for block in content:
        if isinstance(block, dict):
            text = block.get("text", "")
            total += estimate_tokens(text)
    return total


def truncate_text(text: str, max_chars: int = 1000, suffix: str = "...") -> str:
    """Truncate text to max characters."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(suffix)] + suffix


def strip_think(text: str) -> str:
    """
    Strip <think>...</think> and similar reasoning tags from text.
    Used to clean LLM output before displaying to users.
    """
    # Well-formed blocks
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"^[\s]*?<think>[\s\S]*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"<thought>[\s\S]*?</thought>", "", text)
    # Malformed opening tags
    text = re.sub(r"<think[a-zA-Z0-9_\-:]*(?![a-zA-Z0-9_\-])", "", text)
    text = re.sub(r"<thought[a-zA-Z0-9_\-:]*(?![a-zA-Z0-9_\-])", "", text)
    # Partial control tags at edges
    text = re.sub(r"^<thi$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^<tho$", "", text, flags=re.MULTILINE)
    return text.strip()


def ensure_dir(path: Path) -> None:
    """Ensure a directory exists."""
    path.mkdir(parents=True, exist_ok=True)


def safe_filename(name: str) -> str:
    """Convert a string to a safe filename."""
    keepcharacters = (" ", ".", "_", "-")
    return "".join(c if c.isalnum() or c in keepcharacters else "_" for c in name).strip()


def run_shell(
    command: str,
    timeout: int = 30,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """
    Run a shell command and return (exit_code, stdout, stderr).

    This is a synchronous wrapper around asyncio.create_subprocess_exec.
    """
    if sys.platform == "win32":
        shell = True
        cmd = command
    else:
        shell = False
        cmd = ["/bin/sh", "-c", command]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env or os.environ.copy(),
            shell=shell,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


async def run_shell_async(
    command: str,
    timeout: int = 30,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a shell command asynchronously."""
    if sys.platform == "win32":
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env or None,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env or None,
        )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", f"Command timed out after {timeout}s"


def web_search(query: str, num_results: int = 5) -> list[dict[str, str]]:
    """
    Perform a web search using DuckDuckGo.

    Returns list of dicts with 'title', 'url', 'snippet'.
    """
    try:
        from ddgs import DDGS
        results = []
        with DDGS() as ddgs:
            for i, r in enumerate(ddgs.text(query, max_results=num_results)):
                if i >= num_results:
                    break
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        return results
    except ImportError:
        return [{"error": "ddgs not installed"}]
    except Exception as e:
        return [{"error": str(e)}]


def fetch_url(url: str, timeout: int = 10) -> str:
    """Fetch content from a URL."""
    import httpx
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        return response.text
    except Exception as e:
        return f"Error fetching URL: {e}"


def get_page_content(url: str) -> str:
    """Get readable content from a web page."""
    try:
        import httpx
        from readability import Document

        response = httpx.get(url, timeout=10, follow_redirects=True)
        response.raise_for_status()
        doc = Document(response.text)
        return doc.summary()
    except ImportError:
        return fetch_url(url)
    except Exception as e:
        return f"Error: {e}"


def count_tokens(text: str) -> int:
    """
    Estimate token count using a simple heuristic.
    Since tiktoken may not be available on ARM32, we use a rough estimate.
    """
    # Roughly 4 chars per token for English
    return max(1, len(text) // 4)


def format_duration(seconds: float) -> str:
    """Format seconds into human readable duration."""
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"
