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
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_call_id: str
    tool_name: str
    content: str
    success: bool = True
    error: str | None = None


@dataclass
class InboundMessage:
    """An inbound message from a chat channel."""
    platform: str
    user_id: str
    chat_id: str
    text: str
    message_id: str = "0"
    username: str = ""
    first_name: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def session_key(self) -> str:
        return f"{self.platform}:{self.user_id}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """An outbound message to a chat channel."""
    chat_id: str
    text: str = ""
    reply_to: str | None = None
    action: str | None = None  # "typing", "upload_photo", etc.
    parse_mode: str = "markdown"
    message_id: int | None = None

    @property
    def session_key(self) -> str:
        return self.chat_id


@dataclass
class ToolCallEvent:
    """A tool call event."""
    tool_name: str
    arguments: dict[str, Any]
    user_id: str = ""
    chat_id: str = ""


@dataclass
class ToolResultEvent:
    """A tool result event."""
    tool_name: str
    result: str
    success: bool = True