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

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self.tool_calls: asyncio.Queue[ToolCallEvent] = asyncio.Queue()
        self.tool_results: asyncio.Queue[ToolResultEvent] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message."""
        return await self.outbound.get()

    async def publish_tool_call(self, event: ToolCallEvent) -> None:
        """Publish a tool call event."""
        await self.tool_calls.put(event)

    async def publish_tool_result(self, event: ToolResultEvent) -> None:
        """Publish a tool result event."""
        await self.tool_results.put(event)

    def inbound_size(self) -> int:
        return self.inbound.qsize()

    def outbound_size(self) -> int:
        return self.outbound.qsize()

    def task_done(self) -> None:
        """Mark an inbound task as done."""
        self.inbound.task_done()
