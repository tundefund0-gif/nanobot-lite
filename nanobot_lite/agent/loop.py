"""Advanced agent loop: multi-turn reasoning, self-healing, auto-debug, code improvement."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from nanobot_lite.agent.memory import ContextBuilder, Session, SessionStore
from nanobot_lite.bus.events import (
    InboundMessage,
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
    def __init__(self, msgs_per_min: int = 20, tokens_per_min: int = 100000, turns_per_hour: int = 50):
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


@dataclass
class StreamConfig:
    enabled: bool = True
    typing_interval: float = 3.0
    chunk_size: int = 50


# ─── Self-healing helpers ───────────────────────────────────────────────────

class SelfHealer:
    """Analyze errors and propose fixes."""

    ERROR_PATTERNS = [
        (r"SyntaxError", "Fix the Python syntax error shown in the traceback."),
        (r"NameError", "Fix the undefined variable name — check spelling and imports."),
        (r"TypeError", "Fix the type mismatch — check argument types and values."),
        (r"ImportError|ModuleNotFoundError", "Fix the import — check the module name and installation."),
        (r"FileNotFoundError|No such file", "Fix the file path — check if the file exists."),
        (r"TimeoutExpired|timed out", "Increase timeout or simplify the operation."),
        (r"Permission denied", "Fix permissions — check file/directory access rights."),
        (r"Connection error|timeout|Network", "Check network connectivity and retry."),
        (r"IndexError", "Fix index out of range — check list bounds."),
        (r"KeyError", "Fix missing dictionary key — verify the key exists."),
    ]

    @classmethod
    def diagnose(cls, error_text: str) -> str:
        """Diagnose error and suggest fix."""
        for pattern, suggestion in cls.ERROR_PATTERNS:
            if re.search(pattern, error_text, re.IGNORECASE):
                return f"🔧 *Diagnosis:* {suggestion}"
        return f"🔧 *Diagnosis:* Review the error and fix the issue."

    @classmethod
    def generate_fix_prompt(cls, error_text: str, code: str) -> str:
        """Generate a prompt for the LLM to fix the code."""
        diagnosis = cls.diagnose(error_text)
        return (
            f"{diagnosis}\n\n"
            f"Previous code had an error. Write corrected code:\n\n"
            f"Error: {error_text[:500]}\n"
            f"Code:\n{code[:1000]}"
        )


# ─── Tool result formatter ──────────────────────────────────────────────────

class ToolResult:
    def __init__(self, content: str = "", success: bool = True, error: str = ""):
        self.content = content
        self.success = success
        self.error = error


def _format_tool_result(tool_name: str, result: ToolResult) -> str:
    if not result.success:
        return f"❌ {tool_name}: {result.content}"
    content = result.content
    if len(content) > 3000:
        content = content[:3000] + "\n\n... (truncated)"
    return f"✅ {tool_name}:\n{content}"


# ─── Advanced Agent Loop ───────────────────────────────────────────────────

class AgentLoop:
    """
    Advanced agent with:
    - Multi-turn reasoning (think step by step)
    - Self-healing (auto-diagnose & fix errors)
    - Auto-debug (analyze stacktraces)
    - Code improvement (refactor and optimize)
    - Auto-approve all tool executions (no confirmation)
    - Context compression on overflow
    - Retry with exponential backoff
    - Multi-tool chaining
    """

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
        self._stats: dict[str, Any] = {
            "total_turns": 0,
            "total_tokens": 0,
            "tool_calls": 0,
            "heals": 0,
            "auto_fixes": 0,
        }

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict[str, Any]:
        return self._stats.copy()

    async def run(self) -> None:
        self._running = True
        logger.info("Advanced agent loop started")

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
        start_time = time.time()

        # Rate limit check
        ok, reason = self.rate_limiter.check(inbound.user_id or "anon")
        if not ok:
            await self.bus.outbound.put(OutboundMessage(
                chat_id=inbound.chat_id,
                text=reason,
                reply_to=inbound.message_id,
            ))
            return

        # Load session
        session = self.store.get_or_create(
            platform=inbound.platform,
            user_id=inbound.user_id,
            chat_id=inbound.chat_id,
        )

        # Build system prompt with auto-approve instructions
        system_override = self._build_auto_system_prompt()
        full_system = system_override + "\n\n" + self.config.agent.system_prompt

        # Compress if needed
        ctx_tokens = self._estimate_tokens(session.messages)
        max_ctx = self.config.agent.max_tokens * 3
        if ctx_tokens > max_ctx:
            session.compress()

        # Add user message
        session.add_message(role="user", content=inbound.text)

        # Send thinking indicator
        await self.bus.outbound.put(OutboundMessage(
            chat_id=inbound.chat_id,
            action="typing",
        ))

        # Multi-turn: keep going until no more tool calls
        max_inner_turns = 10
        inner_turns = 0
        tool_log = []

        while inner_turns < max_inner_turns:
            inner_turns += 1

            # Build context
            ctx = ContextBuilder.build(
                session=session,
                system_prompt=full_system,
                max_tokens=self.config.agent.max_tokens,
            )
            tools = self.tools.list_for_llm()

            # Call LLM
            response = await self._call_llm_with_retry(ctx, tools)

            # Track tokens
            if response.usage:
                inp = response.usage.get("input_tokens", 0)
                out = response.usage.get("output_tokens", 0)
                self._stats["total_tokens"] += inp + out

            # Strip think tags
            clean_content = strip_think(response.content)

            # Add assistant response to session
            session.add_message(role="assistant", content=clean_content)

            # No tool calls — we're done
            if not response.tool_calls:
                break

            # Execute all tool calls in this turn
            for tc in response.tool_calls:
                self._stats["tool_calls"] += 1
                result = await self._execute_tool_auto(tc, session, tool_log)
                tool_log.append((tc.name, result))

                # Add tool result to session
                session.add_message(
                    role="user",
                    content=f"[TOOL: {tc.name}]\n{result.content}",
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                )

        # Save session
        session.mark_updated()
        self.store.save(session)

        # Build reply — combine assistant response with tool results
        reply_parts = []
        if clean_content and clean_content.strip():
            reply_parts.append(clean_content)

        if tool_log:
            tool_section = ""
            for name, result in tool_log:
                tool_section += _format_tool_result(name, result) + "\n"
            reply_parts.append(tool_section)

        reply_text = "\n\n".join(reply_parts)
        self._stats["total_turns"] += 1

        elapsed = time.time() - start_time
        logger.info(
            f"Turn {self._stats['total_turns']} ({inner_turns} inner): "
            f"{len(response.content)} chars, {self._stats['tool_calls']} tools, {elapsed:.1f}s"
        )

        if reply_text:
            await self.bus.outbound.put(OutboundMessage(
                chat_id=inbound.chat_id,
                text=reply_text,
                reply_to=inbound.message_id,
            ))

    async def _execute_tool_auto(self, tool_call, session: Session, tool_log: list) -> ToolResult:
        """Execute a tool call with auto-approval (no confirmation needed)."""
        tool_name = tool_call.name
        args = tool_call.arguments or {}

        logger.info(f"Auto-executing tool: {tool_name} with args: {list(args.keys())}")

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

            # Self-healing: if tool failed, add suggestion
            if not result.success:
                diag = SelfHealer.diagnose(result.content)
                result.content = f"{result.content}\n\n{diag}"
                self._stats["heals"] += 1

            return ToolResult(content=result.content, success=result.success)

        except Exception as e:
            logger.error(f"Tool {tool_name} error: {e}")
            diag = SelfHealer.diagnose(str(e))
            return ToolResult(
                content=f"Error executing {tool_name}: {e}\n\n{diag}",
                success=False,
                error=str(e),
            )

    async def _call_llm_with_retry(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None,
    ) -> LLMResponse:
        retries = 3
        last_error = ""

        for attempt in range(retries):
            try:
                return await self.provider.chat(
                    messages=messages,
                    tools=tools,
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

    def _build_auto_system_prompt(self) -> str:
        """Build the auto-approve system instructions."""
        return """You are Nanobot-Lite — an advanced autonomous AI agent.

## AUTO-APPROVE MODE
You have FULL authority to execute any tool without asking for permission.
Never ask "Should I run this?" or "Do you want me to?". Just do it.
Every tool execution is automatically approved.

## MULTI-TURN REASONING
For complex tasks, think step by step. Use your tools to gather information,
verify results, and iterate. Don't try to do everything in one response.

## SELF-HEALING
If a tool fails or returns an error:
1. Read the error carefully
2. Use SelfHealer.diagnose() to understand what went wrong
3. Fix the issue and retry with corrected parameters
4. If the first approach doesn't work, try a different approach

## CODE EXECUTION
- Write clean, correct code. Test it before presenting.
- If code has an error, analyze the error and write a fixed version.
- Run code using the run_code tool for Python, JS, Ruby, PHP, Bash, Lua, C.
- Use shell tool for system commands.
- Use calculator, encode_decode, and other utility tools freely.

## AUTO-DEBUG
When you see a traceback or error:
1. Identify the error type (SyntaxError, NameError, TypeError, etc.)
2. Find the exact line and character position
3. Fix the root cause, not just the symptoms
4. Verify the fix works

## CODE IMPROVEMENT
When improving code:
- Make it more readable and maintainable
- Fix bugs and edge cases
- Optimize performance where possible
- Add proper error handling

## TOOL PHILOSOPHY
- Use tools proactively — don't just wait for the user to ask
- Chain multiple tools to accomplish complex goals
- Verify tool results before moving to the next step
- If something doesn't exist, create it. If something is wrong, fix it.

Be bold. Be fast. Be helpful."""

    def _estimate_tokens(self, messages: list) -> int:
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            total += len(content) // 4
        return total