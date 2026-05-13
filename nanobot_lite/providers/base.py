"""LLM Provider base classes."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class ToolCallRequest:
    """A tool call requested by the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """A message in the conversation."""
    role: str  # "user", "assistant", "system"
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_name:
            d["tool_name"] = self.tool_name
        return d


@dataclass
class LLMResponse:
    """Response from an LLM."""
    content: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    reasoning: str | None = None
    usage: dict[str, int] = field(default_factory=dict)  # input_tokens, output_tokens, etc.
    model: str = ""
    stop_reason: str | None = None

    @property
    def text(self) -> str:
        return self.content


@dataclass
class StreamDelta:
    """A chunk of streaming response."""
    content: str = ""
    tool_call: ToolCallRequest | None = None
    done: bool = False


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    name: str = "base"
    supports_streaming: bool = False
    supports_tools: bool = True

    @abstractmethod
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
        """Send a chat request to the LLM."""
        ...

    async def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[StreamDelta]:
        """Stream a chat response. Override for streaming support."""
        # Default: non-streaming fallback
        response = await self.chat(messages, tools, model, max_tokens, temperature, stream=False)
        yield StreamDelta(content=response.content, done=True)

    @property
    def requires_api_key(self) -> bool:
        return True
