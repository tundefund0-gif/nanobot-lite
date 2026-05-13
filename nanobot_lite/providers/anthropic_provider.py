"""Anthropic Claude LLM provider."""
from __future__ import annotations

import os
from typing import Any, AsyncIterator

from loguru import logger

from nanobot_lite.providers.base import (
    LLMProvider,
    LLMResponse,
    Message,
    StreamDelta,
    ToolCallRequest,
)


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider using the official SDK."""

    name = "anthropic"
    supports_streaming = True
    supports_tools = True

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model
        self._client = None

    @property
    def requires_api_key(self) -> bool:
        return not bool(self.api_key)

    def _get_client(self):
        """Lazy-load the Anthropic client."""
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

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
        """Send a chat request to Claude."""
        client = self._get_client()
        model = model or self.model

        # Build message dicts
        msg_dicts = [m.to_dict() for m in messages]

        # Build request
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": msg_dicts,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if tools:
            request_kwargs["tools"] = tools

        # Filter out None values
        request_kwargs = {k: v for k, v in request_kwargs.items() if v is not None}

        try:
            if stream:
                # Streaming handled by chat_stream
                response = await client.messages.create(**request_kwargs)
            else:
                response = await client.messages.create(**request_kwargs)

            # Parse response
            content_blocks = response.content
            text_content = ""
            tool_calls = []

            for block in content_blocks:
                if hasattr(block, "text") and block.text:
                    text_content += block.text
                if hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append(ToolCallRequest(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    ))

            return LLMResponse(
                content=text_content,
                tool_calls=tool_calls,
                usage={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
                model=response.model,
                stop_reason=response.stop_reason,
            )

        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            raise

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[StreamDelta]:
        """Stream a chat response from Claude."""
        client = self._get_client()
        model = model or self.model

        msg_dicts = [m.to_dict() for m in messages]

        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": msg_dicts,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        if tools:
            request_kwargs["tools"] = tools

        request_kwargs = {k: v for k, v in request_kwargs.items() if v is not None}

        try:
            async with client.messages.stream(**request_kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        if hasattr(event.delta, "text") and event.delta.text:
                            yield StreamDelta(content=event.delta.text)
                        elif hasattr(event.delta, "name"):
                            # Tool call start
                            yield StreamDelta(
                                tool_call=ToolCallRequest(
                                    id=getattr(event.delta, "id", ""),
                                    name=event.delta.name,
                                    arguments={},
                                )
                            )
                        elif hasattr(event.delta, "input_json"):
                            # Tool input chunk
                            pass  # accumulate in tool_delta buffer
                    elif event.type == "message_delta":
                        if event.delta.stop_reason:
                            yield StreamDelta(done=True)

        except Exception as e:
            logger.error(f"Anthropic streaming error: {e}")
            yield StreamDelta(content=f"\n\nError: {e}", done=True)
