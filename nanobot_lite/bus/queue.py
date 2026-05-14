"""Async message queue for decoupled channel-agent communication."""
import asyncio
from typing import Any

from nanobot_lite.bus.events import (
    InboundMessage,
    OutboundMessage,
    ToolCallEvent,
    ToolResultEvent,
)


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self, maxsize: int = 100):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=maxsize)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(maxsize=maxsize)
        self.tool_calls: asyncio.Queue[ToolCallEvent] = asyncio.Queue(maxsize=maxsize)
        self.tool_results: asyncio.Queue[ToolResultEvent] = asyncio.Queue(maxsize=maxsize)

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        await self.inbound.put(msg)

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)

    async def publish_tool_call(self, event: ToolCallEvent) -> None:
        """Publish a tool call event."""
        await self.tool_calls.put(event)

    async def publish_tool_result(self, event: ToolResultEvent) -> None:
        """Publish a tool result event."""
        await self.tool_results.put(event)