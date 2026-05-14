"""OpenCode Zen provider — OpenAI-compatible API via raw HTTP (no native deps)."""
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

DEFAULT_BASE_URL = "https://opencode.ai/zen"
DEFAULT_MODEL = "minimax-m2.5-free"


class OpenCodeZenProvider(LLMProvider):
    """OpenCode Zen provider using OpenAI-compatible /v1/chat/completions API.

    Works everywhere — pure Python stdlib HTTP, no native deps.
    Compatible with any OpenAI-API-like endpoint (OpenRouter, custom, etc.).
    """

    name = "opencode-zen"
    supports_streaming = True
    supports_tools = True

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
    ):
        self.api_key = api_key or os.environ.get("OPENCODE_API_KEY", "")
        self.model = model
        self.base_url = base_url.rstrip("/")

    @property
    def requires_api_key(self) -> bool:
        return not bool(self.api_key)

    def _build_url(self, path: str) -> str:
        return f"{self.base_url}/v1{path}"

    def _messages_to_openai(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert our Message objects to OpenAI chat format."""
        result = []
        for m in messages:
            if m.tool_name and m.tool_call_id:
                # Tool result message
                result.append({
                    "role": m.role,
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_call_id": m.tool_call_id,
                            "content": m.content,
                        }
                    ],
                })
            elif m.tool_call_id:
                # Tool call message (reasoning models sometimes emit this)
                result.append({
                    "role": m.role,
                    "content": m.content,
                    "tool_call_id": m.tool_call_id,
                })
            else:
                result.append({"role": m.role, "content": m.content})
        return result

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
        """Send a chat request to OpenCode Zen via OpenAI-compatible API."""
        model = model or self.model

        # Build OpenAI-compatible request body
        body: dict[str, Any] = {
            "model": model,
            "messages": self._messages_to_openai(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if tools:
            body["tools"] = tools

        if stream:
            body["stream"] = True

        body_json = json.dumps(body, ensure_ascii=False)

        url = self._build_url("/chat/completions")
        req = urllib.request.Request(url, data=body_json.encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenCode Zen API error {e.code}: {error_body}") from e

        # Parse OpenAI-compatible response
        choices = data.get("choices", [])
        first_choice = choices[0] if choices else {}

        message = first_choice.get("message", {})
        text_content = message.get("content", "")

        # Extract tool calls
        tool_calls = []
        raw_tool_calls = message.get("tool_calls", [])
        for tc in raw_tool_calls:
            func = tc.get("function", {})
            tool_calls.append(ToolCallRequest(
                id=tc.get("id", ""),
                name=func.get("name", ""),
                arguments=func.get("arguments", {}),
            ))

        # OpenCode Zen may return reasoning as a special content block or field
        reasoning = None
        if isinstance(text_content, list):
            # Structured content — extract reasoning if present
            for block in text_content:
                if isinstance(block, dict) and block.get("type") == "reasoning":
                    reasoning = block.get("text") or block.get("content", "")
                    break
            # Flatten text blocks
            text_content = "".join(
                b.get("text", "") if isinstance(b, dict) and b.get("type") == "text" else str(b)
                for b in text_content
            )

        return LLMResponse(
            content=str(text_content) if text_content else "",
            tool_calls=tool_calls,
            reasoning=reasoning,
            usage=data.get("usage", {}),
            model=data.get("model", model),
            stop_reason=first_choice.get("finish_reason"),
        )

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[StreamDelta]:
        """Stream a chat response from OpenCode Zen via SSE."""
        model = model or self.model

        body: dict[str, Any] = {
            "model": model,
            "messages": self._messages_to_openai(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        if tools:
            body["tools"] = tools

        body_json = json.dumps(body, ensure_ascii=False)

        url = self._build_url("/chat/completions")
        req = urllib.request.Request(url, data=body_json.encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Accept", "text/event-stream")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                buffer: list[bytes] = []
                for chunk in resp:
                    buffer.append(chunk)
                    raw = b"".join(buffer)
                    text = raw.decode("utf-8", errors="replace")
                    lines = text.split("\n")

                    for line in lines:
                        stripped = line.strip()
                        if not stripped or stripped == "data: [DONE]":
                            if stripped == "data: [DONE]":
                                yield StreamDelta(done=True)
                                return
                            continue

                        if stripped.startswith("data: "):
                            payload = stripped[6:]
                        else:
                            continue

                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            # Incomplete JSON, keep buffering
                            continue

                        choices = event.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})

                        # Text content
                        content = delta.get("content", "")
                        if content:
                            yield StreamDelta(content=content)
                            buffer.clear()
                            break

                        # Tool calls
                        tool_calls = delta.get("tool_calls", [])
                        for tc in tool_calls:
                            func = tc.get("function", {})
                            yield StreamDelta(
                                tool_call=ToolCallRequest(
                                    id=tc.get("id", ""),
                                    name=func.get("name", ""),
                                    arguments=func.get("arguments", {}),
                                )
                            )
                            buffer.clear()
                            break

                        # Stop reason
                        finish = choices[0].get("finish_reason")
                        if finish:
                            yield StreamDelta(done=True)
                            return

        except Exception as e:
            logger.error(f"OpenCode Zen streaming error: {e}")
            yield StreamDelta(content=f"\n\nError: {e}", done=True)


# ─── Provider factory ────────────────────────────────────────────────────────

def create_provider(
    provider_type: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
) -> LLMProvider:
    """Factory to create the right provider from config.

    Args:
        provider_type: "opencode-zen" | "anthropic"
        api_key: API key string
        model: model name
        base_url: optional override (used by opencode-zen)
    """
    if provider_type == "opencode-zen":
        return OpenCodeZenProvider(
            api_key=api_key,
            model=model,
            base_url=base_url or DEFAULT_BASE_URL,
        )
    elif provider_type == "anthropic":
        from nanobot_lite.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key, model=model)
    else:
        raise ValueError(f"Unknown provider type: {provider_type!r}. "
                         f"Known: 'opencode-zen', 'anthropic'")
