"""Event types for the message bus."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass
class Message:
    """A single message in the conversation."""
    role: MessageRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        d = {"role": self.role.value, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_name:
            d["tool_name"] = self.tool_name
        return d


@dataclass
class ToolCall:
    """A tool call requested by the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_call_id: str
    tool_name: str
    content: str
    success: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "content": self.content,
            "is_error": self.error is not None,
        }


@dataclass
class InboundMessage:
    """An inbound message from a chat channel."""
    session_key: str
    user_id: str
    chat_id: str
    message: Message
    message_id: int = 0
    reply_to: int | None = None
    attachments: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def __str__(self) -> str:
        return f"Inbound[{self.session_key}] {self.message.role.value}: {self.message.content[:50]}"


@dataclass
class OutboundMessage:
    """An outbound message to a chat channel."""
    session_key: str
    chat_id: str
    content: str
    reply_to: int | None = None
    is_streaming: bool = False
    parse_mode: str = "markdown"
    message_id: int | None = None  # for edits
    attachments: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"Outbound[{self.session_key}]: {self.content[:50]}"


@dataclass
class ToolCallEvent:
    """A tool call event (used for streaming/progress)."""
    session_key: str
    tool_call: ToolCall
    partial_arguments: str = ""  # for streaming


@dataclass
class ToolResultEvent:
    """A tool result event."""
    session_key: str
    result: ToolResult
