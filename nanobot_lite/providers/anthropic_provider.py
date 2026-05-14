"""Anthropic Claude LLM provider — pure HTTP (no Rust, no jiter, no native deps)."""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any, AsyncIterator

try:
    from loguru import logger
except ImportError:
    import sys as _sys
    class _Dummy:
        def debug(self, *a, **k): pass
        def info(self, *a, **k): pass
        def warning(self, *a, **k): print(*a, file=_sys.stderr)
        def error(self, *a, **k): print(*a, file=_sys.stderr)
    logger = _Dummy()

from nanobot_lite.providers.base import (
    LLMProvider,
    LLMResponse,
    Message,
    StreamDelta,
    ToolCallRequest,
)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def _chunk_json(chunks: list[bytes]) -> str:
    """Reconstruct JSON from SSE data chunks."""
    text = b"".join(chunks).decode("utf-8", errors="replace")

    # Remove SSE format: "data: {...}\n\n"
    lines = text.split("\n")
    result_parts = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("data: "):
            result_parts.append(stripped[6:])
        elif stripped == "":
            # Empty line — could be end of event
            pass

    return "".join(result_parts)


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider using raw HTTP — works everywhere, no native deps."""

    name = "anthropic"
    supports_streaming = True
    supports_tools = True

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model

    @property
    def requires_api_key(self) -> bool:
        return not bool(self.api_key)

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
        **kwargs,
    ) -> LLMResponse:
        """Send a chat request to Claude via raw HTTP."""
        model = model or self.model

        # Build message dicts for Anthropic format
        msg_dicts = []
        for m in messages:
            d: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            if m.tool_name:
                d["content"] = [
                    {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                ]
            msg_dicts.append(d)

        # Build request body
        body: dict[str, Any] = {
            "model": model,
            "messages": msg_dicts,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if tools:
            body["tools"] = tools

        body_json = json.dumps(body, ensure_ascii=False)

        # Build request
        req = urllib.request.Request(
            ANTHROPIC_API_URL,
            data=body_json.encode("utf-8"),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("x-api-key", self.api_key)
        req.add_header("anthropic-version", "2023-06-01")
        req.add_header("accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anthropic API error {e.code}: {error_body}") from e

        # Parse response
        content_blocks = data.get("content", [])
        text_content = ""
        tool_calls = []

        for block in content_blocks:
            if block.get("type") == "text":
                text_content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(ToolCallRequest(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input", {}),
                ))

        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            usage=data.get("usage", {}),
            model=data.get("model", model),
            stop_reason=data.get("stop_reason"),
        )

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[StreamDelta]:
        """Stream a chat response from Claude via Server-Sent Events."""
        model = model or self.model

        # Build message dicts
        msg_dicts = []
        for m in messages:
            d: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_call_id:
                d["content"] = [
                    {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                ]
            msg_dicts.append(d)

        body: dict[str, Any] = {
            "model": model,
            "messages": msg_dicts,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        if tools:
            body["tools"] = tools

        body_json = json.dumps(body, ensure_ascii=False)

        req = urllib.request.Request(
            ANTHROPIC_API_URL,
            data=body_json.encode("utf-8"),
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("x-api-key", self.api_key)
        req.add_header("anthropic-version", "2023-06-01")
        req.add_header("accept", "text/event-stream")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                buffer: list[bytes] = []
                for chunk in resp:
                    buffer.append(chunk)

                    # Try to parse complete SSE lines from buffer
                    raw = b"".join(buffer)
                    text = raw.decode("utf-8", errors="replace")
                    lines = text.split("\n")

                    for line in lines:
                        stripped = stripped.lstrip("data: ").strip() if line.startswith("data:") else None
                        if stripped is None:
                            continue
                        if not stripped or stripped == "[DONE]":
                            continue

                        try:
                            event = json.loads(stripped)
                        except json.JSONDecodeError:
                            # Incomplete JSON, keep buffering
                            continue

                        etype = event.get("type", "")

                        if etype == "content_block_delta":
                            delta = event.get("delta", {})
                            dtype = delta.get("type", "")

                            if dtype == "text_delta":
                                text_chunk = delta.get("text", "")
                                if text_chunk:
                                    yield StreamDelta(content=text_chunk)
                                    buffer.clear()
                                    break

                            elif dtype == "input_json_delta":
                                # Tool input chunk — accumulate
                                pass

                        elif etype == "message_delta":
                            if event.get("delta", {}).get("stop_reason"):
                                yield StreamDelta(done=True)
                                return

        except Exception as e:
            logger.error(f"Anthropic streaming error: {e}")
            yield StreamDelta(content=f"\n\nError: {e}", done=True)