"""Advanced agent loop with retry, streaming, tool chaining, and turn tracking."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from nanobot_lite.agent.memory import ContextBuilder, Session, SessionStore
from nanobot_lite.bus.events import (
    InboundMessage,
    Message,
    OutboundMessage,
    ToolCallEvent,
    ToolResultEvent,
)
from nanobot_lite.bus.queue import MessageBus
from nanobot_lite.config.schema import Config
from nanobot_lite.providers.base import LLMProvider, LLMResponse, Message as LLMMessage
from nanobot_lite.tools.base import ToolRegistry
from nanobot_lite.utils.helpers import strip_think


# ─── Rate limiter ───────────────────────────────────────────────────────────

class TokenBucket:
    """Simple token bucket rate limiter per user."""

    def __init__(self, rate: float = 20.0, per: float = 60.0):
        self.rate = rate / per
        self.capacity = rate
        self._tokens = self.capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def consume(self, tokens: float = 1.0) -> bool:
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


class RateLimiter:
    """Per-user rate limiter."""

    def __init__(
        self,
        msgs_per_min: int = 20,
        tokens_per_min: int = 100000,
        turns_per_hour: int = 50,
    ):
        self.msg_limiter = TokenBucket(float(msgs_per_min), 60.0)
        self.token_limiter = TokenBucket(float(tokens_per_min), 60.0)
        self.turn_limiter = TokenBucket(float(turns_per_hour), 3600.0)
        self._user_buckets: dict[str, dict] = {}

    def _user(self, user_id: str) -> dict:
        if user_id not in self._user_buckets:
            self._user_buckets[user_id] = {
                "msg": TokenBucket(20.0, 60.0),
                "token": TokenBucket(100000.0, 60.0),
                "turn": TokenBucket(50.0, 3600.0),
            }
        return self._user_buckets[user_id]

    def check(self, user_id: str) -> tuple[bool, str]:
        buckets = self._user(user_id)
        if not buckets["msg"].consume(1):
            return False, "⏳ Rate limit: max 20 messages/min. Slow down!"
        if not buckets["token"].consume(500):
            return False, "⏳ Rate limit: too many tokens/min. Wait a minute!"
        if not buckets["turn"].consume(1):
            return False, "⏳ Rate limit: max 50 turns/hour. Come back soon!"
        return True, ""


# ─── Streaming config ───────────────────────────────────────────────────────

@dataclass
class StreamConfig:
    enabled: bool = True
    typing_interval: float = 3.0
    chunk_size: int = 50


# ─── Tool result formatter ──────────────────────────────────────────────────

class ToolResult:
    """Result of a tool execution."""
    def __init__(self, content: str = "", success: bool = True, error: str = ""):
        self.content = content
        self.success = success
        self.error = error


def _format_tool_result(tool_name: str, result: ToolResult) -> str:
    """Format tool result for display."""
    if not result.success:
        return f"❌ {tool_name}: {result.content}"
    content = result.content
    if len(content) > 3000:
        content = content[:3000] + "\n\n... (truncated)"
    return f"✅ {tool_name}:\n{content}"


# ─── Agent Loop ─────────────────────────────────────────────────────────────

class AgentLoop:
    """Advanced agent loop with retry, tool chaining, context compression."""

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        config: Config,
        tool_registry: ToolRegistry,
        session_store: SessionStore,
        rate_limiter: RateLimiter | None = None,
        stream_config: StreamConfig | None = None,
    ):
        self.bus = bus
        self.provider = provider
        self.config = config
        self.tools = tool_registry
        self.store = session_store
        self.rate_limiter = rate_limiter or RateLimiter()
        self.stream = stream_config or StreamConfig()
        self._running = False
        self._stats: dict[str, Any] = {"total_turns": 0, "total_tokens": 0, "tool_calls": 0}

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict[str, Any]:
        return self._stats.copy()

    async def run(self) -> None:
        """Main loop — consume messages from bus."""
        self._running = True
        logger.info("Agent loop started")

        while self._running:
            try:
                event = await self.bus.inbound.get()
                if not isinstance(event, InboundMessage):
                    continue
                await self._handle_message(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Agent loop error: {e}")

    async def _handle_message(self, inbound: InboundMessage) -> None:
        """Handle a single inbound message."""
        start_time = time.time()

        # Rate limit
        ok, reason = self.rate_limiter.check(inbound.user_id or "anon")
        if not ok:
            await self.bus.outbound.put(OutboundMessage(
                chat_id=inbound.chat_id,
                text=reason,
                reply_to=inbound.message_id,
            ))
            return

        # Turn limit
        if self._stats["total_turns"] >= self.config.agent.max_turns:
            await self.bus.outbound.put(OutboundMessage(
                chat_id=inbound.chat_id,
                text="⛔ Turn limit reached. Restart the bot to continue.",
                reply_to=inbound.message_id,
            ))
            return

        # Load session
        session = self.store.get_or_create(
            platform=inbound.platform,
            user_id=inbound.user_id,
            chat_id=inbound.chat_id,
        )
        session.add_message(role="user", content=inbound.text)

        # Compress if context too large
        ctx_tokens = self._estimate_tokens(session.messages)
        max_ctx = self.config.agent.max_tokens * 3
        if ctx_tokens > max_ctx:
            session.compress()
            logger.info(f"Context compressed: ~{ctx_tokens} → {self._estimate_tokens(session.messages)} tokens")

        # Build LLM messages
        ctx = ContextBuilder.build(
            session=session,
            system_prompt=self.config.agent.system_prompt,
            max_tokens=self.config.agent.max_tokens,
        )
        tools = self.tools.list_for_llm()

        # Typing indicator
        await self.bus.outbound.put(OutboundMessage(chat_id=inbound.chat_id, action="typing"))

        # Call LLM with retry
        response = await self._call_llm_with_retry(ctx, tools)

        # Update stats
        self._stats["total_turns"] += 1
        if response.usage:
            inp = response.usage.get("input_tokens", 0)
            out = response.usage.get("output_tokens", 0)
            self._stats["total_tokens"] += inp + out

        # Add response to session
        clean_content = strip_think(response.content)
        session.add_message(role="assistant", content=clean_content)

        # Handle tool calls (multi-tool chaining)
        tool_results_text = ""
        if response.tool_calls:
            for tc in response.tool_calls:
                self._stats["tool_calls"] += 1
                result = await self._execute_tool(tc, session)
                session.add_message(
                    role="user",
                    content=f"[TOOL: {tc.name}] {result.content}",
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                )
                tool_results_text += _format_tool_result(tc.name, result) + "\n"

            # Continue: get next LLM response after tool results
            ctx = ContextBuilder.build(
                session=session,
                system_prompt=self.config.agent.system_prompt,
                max_tokens=self.config.agent.max_tokens,
            )
            response = await self._call_llm_with_retry(ctx, tools)
            clean_content = strip_think(response.content)
            session.add_message(role="assistant", content=clean_content)

        # Save session
        session.mark_updated()
        self.store.save(session)

        # Send reply
        reply_text = clean_content
        if tool_results_text:
            reply_text = f"{reply_text}\n\n{tool_results_text}" if reply_text else tool_results_text

        elapsed = time.time() - start_time
        logger.info(
            f"Turn {self._stats['total_turns']}: {len(response.content)} chars, "
            f"{self._stats['tool_calls']} tools, {elapsed:.1f}s"
        )

        if reply_text:
            await self.bus.outbound.put(OutboundMessage(
                chat_id=inbound.chat_id,
                text=reply_text,
                reply_to=inbound.message_id,
            ))

    async def _call_llm_with_retry(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        """Call LLM with exponential backoff retry."""
        retries = 3
        last_error = ""

        for attempt in range(retries):
            try:
                return await self.provider.chat(
                    messages=messages,
                    tools=tools if tools else None,
                    model=self.config.agent.model,
                    max_tokens=self.config.agent.max_tokens,
                    temperature=self.config.agent.temperature,
                )
            except Exception as e:
                last_error = str(e)
                wait = 2 ** attempt
                logger.warning(f"LLM call failed (attempt {attempt+1}/{retries}): {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(wait)

        return LLMResponse(content=f"❌ AI service unavailable after {retries} attempts: {last_error}")

    async def _execute_tool(self, tool_call, session: Session) -> ToolResult:
        """Execute a tool call."""
        tool_name = tool_call.name
        args = tool_call.arguments or {}

        await self.bus.inbound.put(ToolCallEvent(
            tool_name=tool_name,
            arguments=args,
            user_id=session.user_id,
            chat_id=session.chat_id,
        ))

        try:
            result = await self.tools.execute(tool_name, args)

            await self.bus.inbound.put(ToolResultEvent(
                tool_name=tool_name,
                result=result.content if result.success else result.content,
                success=result.success,
            ))

            return ToolResult(content=result.content, success=result.success)
        except Exception as e:
            logger.error(f"Tool {tool_name} error: {e}")
            return ToolResult(content=str(e), success=False, error=str(e))

    def _estimate_tokens(self, messages: list) -> int:
        """Rough token estimate."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            total += len(content) // 4
        return total