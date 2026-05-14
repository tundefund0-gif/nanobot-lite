"""Advanced agent loop: multi-turn reasoning, deep self-healing, auto-debug, code improvement."""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any

try:
    from loguru import logger
except ImportError:
    import sys as _sys
    class _Dummy:
        def debug(self, *a, **k): pass
        def info(self, *a, **k): pass
        def warning(self, *a, **k): print(*a, file=_sys.stderr)
        def error(self, *a, **k): print(*a, file=_sys.stderr)
        def success(self, *a, **k): pass
    logger = _Dummy()

from nanobot_lite.agent.healer import get_healer, Severity, Strategy, PerErrorCircuitBreaker
from nanobot_lite.agent.memory import ContextBuilder, Session, SessionStore
from nanobot_lite.agent.self_diagnosis import run_diagnostics
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


# ─── Rate limiter ──────────────────────────────────────────────────────────────

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
        self.msg_limiter  = TokenBucket(float(msgs_per_min),   60.0)
        self.token_limiter = TokenBucket(float(tokens_per_min), 60.0)
        self.turn_limiter  = TokenBucket(float(turns_per_hour), 3600.0)
        self._user_buckets: dict[str, dict] = {}

    def _user(self, user_id: str) -> dict:
        if user_id not in self._user_buckets:
            self._user_buckets[user_id] = {
                "msg":   TokenBucket(20.0, 60.0),
                "token": TokenBucket(100000.0, 60.0),
                "turn":  TokenBucket(50.0, 3600.0),
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


# ─── Tool result formatter ─────────────────────────────────────────────────────

class ToolResult:
    def __init__(self, content: str = "", success: bool = True, error: str = ""):
        self.content = content
        self.success = success
        self.error   = error


def _format_tool_result(tool_name: str, result: ToolResult, heal_pass: int = 0) -> str:
    prefix = "✅" if result.success else "❌"
    if heal_pass > 0:
        prefix = f"🔧 [{heal_pass}x] {prefix}"
    content = result.content
    if len(content) > 3000:
        content = content[:3000] + "\n\n... (truncated)"
    return f"{prefix} {tool_name}:\n{content}"


# ─── Advanced Agent Loop ───────────────────────────────────────────────────────

class AgentLoop:
    """
    Advanced agent with deep self-healing capabilities:

    - Multi-turn reasoning (think step by step)
    - Deep self-healing (5-pass iterative healing per tool call)
    - Circuit breaker per tool (skip after repeated failure)
    - Auto-diagnosis (40+ structured error patterns)
    - AST-based Python auto-fix
    - Command fallback chains
    - Path auto-discovery
    - LLM-guided healing for complex errors
    - Rollback on failure
    - Tool health monitoring
    - Self-diagnostic tool
    - Auto-approve all tool executions
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
        self.bus            = bus
        self.provider       = provider
        self.config         = config
        self.tools          = tool_registry
        self.store          = session_store
        self.rate_limiter   = rate_limiter or RateLimiter()
        self.stream         = stream_config or StreamConfig()
        self._running       = False
        self.healer         = get_healer()
        self._stats: dict[str, Any] = {
            "total_turns": 0,
            "total_tokens": 0,
            "tool_calls": 0,
            "heals": 0,
            "auto_fixes": 0,
            "circuit_trips": 0,
            "llm_heals": 0,
        }

    def stop(self) -> None:
        self._running = False

    @property
    def stats(self) -> dict[str, Any]:
        s = self._stats.copy()
        s["healer_health"] = self.healer.health_report()
        return s

    async def run(self) -> None:
        self._running = True
        logger.info("Advanced agent loop started (deep self-healing enabled)")

        while self._running:
            try:
                event = await asyncio.wait_for(self.bus.inbound.get(), timeout=60.0)
                if not isinstance(event, InboundMessage):
                    continue
                await self._handle_message(event)
            except asyncio.TimeoutError:
                continue  # no messages, loop continues
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
                chat_id=inbound.chat_id, text=reason, reply_to=inbound.message_id,
            ))
            return

        # Load session
        session = self.store.get_or_create(
            platform=inbound.platform,
            user_id=inbound.user_id,
            chat_id=inbound.chat_id,
        )

        # Build system prompt with auto-approve + self-healing instructions
        system_override = self._build_system_prompt()
        full_system = system_override + "\n\n" + self.config.agent.system_prompt

        # Compress if needed
        ctx_tokens = self._estimate_tokens(session.messages)
        max_ctx = self.config.agent.max_tokens * 3
        if ctx_tokens > max_ctx:
            session.compress()

        # Add user message
        session.add_message(role="user", content=inbound.text)

        # Send typing indicator
        await self.bus.outbound.put(OutboundMessage(
            chat_id=inbound.chat_id, action="typing",
        ))

        # Multi-turn loop: keep going until no more tool calls
        max_inner_turns = 10
        inner_turns = 0
        tool_log: list[tuple[str, ToolResult, int]] = []

        while inner_turns < max_inner_turns:
            inner_turns += 1

            ctx = ContextBuilder.build(
                session=session,
                system_prompt=full_system,
                max_tokens=self.config.agent.max_tokens,
            )
            tools = self.tools.list_for_llm()

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

            # No tool calls — done
            if not response.tool_calls:
                break

            # Execute all tool calls with deep healing
            for tc in response.tool_calls:
                self._stats["tool_calls"] += 1

                # Check circuit breaker before executing
                if not self.healer.should_heal(tc.name):
                    self._stats["circuit_trips"] += 1
                    circuit_msg = ToolResult(
                        content=(
                            f"⏭️ Circuit breaker OPEN for '{tc.name}' — "
                            "too many recent failures. Skipping.\n"
                            "Run /diagnose circuits to check status."
                        ),
                        success=False,
                    )
                    tool_log.append((tc.name, circuit_msg, 0))
                    session.add_message(
                        role="user",
                        content=f"[TOOL: {tc.name}]\n{circuit_msg.content}",
                        tool_call_id=tc.id,
                        tool_name=tc.name,
                    )
                    continue

                # Deep healing execute
                result, heal_passes = await self._execute_with_healing(tc, session)
                tool_log.append((tc.name, result, heal_passes))

                if heal_passes > 0:
                    self._stats["heals"] += 1

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

        # Build reply
        reply_parts = []
        if clean_content and clean_content.strip():
            reply_parts.append(clean_content)

        if tool_log:
            tool_section = ""
            for name, result, heal_pass in tool_log:
                tool_section += _format_tool_result(name, result, heal_pass) + "\n"
            reply_parts.append(tool_section)

        reply_text = "\n\n".join(reply_parts)
        self._stats["total_turns"] += 1

        elapsed = time.time() - start_time
        logger.info(
            f"Turn {self._stats['total_turns']} ({inner_turns} inner): "
            f"{len(response.content)} chars, {self._stats['tool_calls']} tools, "
            f"heals={self._stats['heals']}, {elapsed:.1f}s"
        )

        if reply_text:
            await self.bus.outbound.put(OutboundMessage(
                chat_id=inbound.chat_id, text=reply_text, reply_to=inbound.message_id,
            ))

    async def _execute_with_healing(
        self, tool_call, session
    ) -> tuple[ToolResult, int]:
        """
        Execute a tool call with deep multi-pass healing.

        Strategy per pass:
          1. Try original args
          2. AST auto-fix (Python errors)
          3. Fallback tool / environment recovery
          4. Path recovery (FileNotFoundError)
          5. Incremental code fix
          6. Retry with exponential backoff
          7. LLM-guided fix (last resort)
        """
        tool_name = tool_call.name
        args = tool_call.arguments or {}

        logger.info(f"[heal] executing {tool_name} with {list(args.keys())}")

        error_history: list[str] = []
        current_args = dict(args)
        current_code = args.get("code", "") if tool_name == "run_code" else ""
        heal_passes = 0
        current_tool = tool_name  # may change if we fall back
        current_error_type = ""

        for pass_num in range(1, self.healer.MAX_HEAL_PASSES + 1):
            heal_passes = pass_num

            # ── Execute ─────────────────────────────────────────────────────────
            await self.bus.inbound.put(ToolCallEvent(
                tool_name=current_tool,
                arguments=current_args,
                user_id=session.user_id,
                chat_id=session.chat_id,
            ))

            try:
                result = await self.tools.execute(current_tool, current_args)
            except Exception as e:
                logger.error(f"[heal] exception in {current_tool}: {e}")
                plan = self.healer.diagnose(str(e))
                current_error_type = self.healer._error_type_cache.get(str(e), "Exception")
                self.healer.record_tool_result(
                    current_tool, success=False, fatal=(plan.severity == Severity.FATAL),
                    error_type=current_error_type
                )
                result = ToolResult(
                    content=f"Exception: {e}\n\n🔧 {plan.diagnosis}",
                    success=False,
                    error=str(e),
                )

            await self.bus.inbound.put(ToolResultEvent(
                tool_name=current_tool,
                result=result.content,
                success=result.success,
            ))

            if result.success:
                self.healer.record_tool_result(
                    current_tool, success=True, heal_passes=pass_num - 1,
                    error_type=current_error_type
                )
                return result, pass_num - 1

            # Failure — diagnose and record
            plan = self.healer.diagnose(result.content)
            current_error_type = self.healer._error_type_cache.get(result.content, "UnknownError")
            error_history.append(result.content[:300])

            if pass_num < self.healer.MAX_HEAL_PASSES:
                logger.info(
                    f"[heal] pass {pass_num}/{self.healer.MAX_HEAL_PASSES} failed for "
                    f"{current_tool} ({current_error_type}), strategy={plan.strategy.name}"
                )

            # ── Healing strategies ───────────────────────────────────────────────

            fixed = False

            # Strategy 1: AST auto-fix for Python (incremental minimal fix first)
            if (current_tool == "run_code" and
                plan.strategy in (Strategy.PATCH_CODE, Strategy.RETRY_PARAMS,
                                  Strategy.INCREMENTAL_FIX) and
                current_code):
                fixed_code, fix_reason = self.healer.auto_fix_code(
                    current_code, result.content
                )
                if fixed_code and fixed_code != current_code:
                    current_code = fixed_code
                    current_args["code"] = fixed_code
                    self._stats["auto_fixes"] += 1
                    logger.info(f"[heal] AST fix applied: {fix_reason}")
                    fixed = True

            # Strategy 2: Fallback tool
            if not fixed and plan.strategy == Strategy.FALLBACK_TOOL:
                fallback = self.healer.get_fallback_tool(current_tool)
                if fallback:
                    logger.info(f"[heal] fallback {current_tool} → {fallback}")
                    old_tool = current_tool
                    current_tool = fallback
                    if fallback == "python":
                        current_args = {"code": args.get("command", ""), "language": "bash"}
                    fixed = True

            # Strategy 3: Environment recovery (PATH, PYTHONPATH, etc.)
            if not fixed and plan.strategy == Strategy.ENVIRONMENT_RECOVERY:
                suggestions = self.healer.path_finder.suggest_env_fixes(result.content)
                if suggestions:
                    plan.fix_hints.extend(suggestions)
                    # Try to fix common env issues
                    if "PYTHONPATH" in result.content:
                        import os
                        import sys
                        # Try to add current dir to sys.path
                        sys.path.insert(0, os.getcwd())
                        logger.info("[heal] added cwd to sys.path")
                        fixed = True

            # Strategy 4: Alternative paths (FileNotFoundError)
            if not fixed and plan.strategy == Strategy.ALT_PATH:
                path_m = re.search(r"['\"]([^'\"]+)['\"]", result.content)
                if path_m:
                    guessed = path_m.group(1)
                    similar = self.healer.path_finder.find_similar(
                        guessed,
                        session.user_id,
                    )
                    if similar:
                        logger.info(f"[heal] path recovery: {similar}")
                        if "path" in current_args:
                            current_args["path"] = similar[0]
                        fixed = True

            # Strategy 5: Retry with backoff (network/timeout)
            if not fixed and plan.strategy == Strategy.RETRY_BACKOFF:
                delay = plan.retry_delay * (2 ** (pass_num - 1)) if plan.retry_delay else min(
                    2 ** pass_num, 30
                )
                logger.info(f"[heal] retry_backoff for {current_tool}, "
                           f"waiting {delay:.1f}s...")
                await asyncio.sleep(delay)
                fixed = True

            # Strategy 6: Generic retry
            if not fixed and plan.strategy == Strategy.RETRY:
                logger.info(f"[heal] retry for {current_tool}")
                await asyncio.sleep(min(2 ** pass_num, 10))
                fixed = True

            # Strategy 7: LLM-guided healing (last two passes)
            if (not fixed and
                (plan.escalate_to_llm or plan.severity >= Severity.HIGH) and
                pass_num >= self.healer.MAX_HEAL_PASSES - 1):
                llm_prompt = self.healer.build_llm_fix_prompt(
                    tool_name=current_tool,
                    args=current_args,
                    error_text=result.content,
                    code=current_code,
                    previous_attempts=error_history,
                )
                llm_response = await self._call_llm_with_retry(
                    [LLMMessage(role="user", content=llm_prompt)],
                    tools=None,
                )
                suggested_fix = strip_think(llm_response.content)
                if suggested_fix and len(suggested_fix) < 3000:
                    code_m = re.search(r"```(?:python)?\n(.*?)```", suggested_fix, re.S)
                    if code_m:
                        current_code = code_m.group(1).strip()
                        current_args["code"] = current_code
                        self._stats["llm_heals"] += 1
                        logger.info("[heal] LLM-guided fix injected")
                        fixed = True

            if not fixed:
                break

        # All healing passes exhausted
        self.healer.record_tool_result(
            current_tool, success=False, heal_passes=heal_passes,
            error_type=current_error_type
        )
        final_plan = self.healer.diagnose(error_history[-1] if error_history else "unknown")
        return ToolResult(
            content=(
                f"{result.content}\n\n"
                f"🔧 *Diagnosis:* {final_plan.diagnosis}\n"
                f"📋 *Fix hints:*\n" +
                "\n".join(f"  • {h}" for h in final_plan.fix_hints) +
                f"\n_Errors: {current_error_type} | Healed {heal_passes}x — max passes reached._"
            ),
            success=False,
            error=result.error or "",
        ), heal_passes

    async def _call_llm_with_retry(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None,
    ) -> LLMResponse:
        retries = 3
        last_error = ""

        for attempt in range(retries):
            try:
                return await asyncio.wait_for(
                    self.provider.chat(
                        messages=messages,
                        tools=tools,
                        model=self.config.agent.model,
                        max_tokens=self.config.agent.max_tokens,
                        temperature=self.config.agent.temperature,
                    ),
                    timeout=90.0,
                )
            except asyncio.TimeoutError:
                last_error = f"LLM call timed out after 90s (attempt {attempt+1}/{retries})"
                logger.warning(last_error)
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                last_error = str(e)
                wait = 2 ** attempt
                logger.warning(f"LLM call failed (attempt {attempt+1}/{retries}): {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(wait)

        return LLMResponse(content=f"❌ AI service unavailable after {retries} attempts: {last_error}")

    def _build_system_prompt(self) -> str:
        return """You are Nanobot-Lite — an advanced autonomous AI agent with deep self-healing.

## AUTO-APPROVE MODE
You have FULL authority to execute any tool without asking for permission.
Never ask "Should I run this?". Just do it.

## MULTI-TURN REASONING
For complex tasks, think step by step. Use your tools to gather information,
verify results, and iterate. Don't try to do everything in one response.

## DEEP SELF-HEALING
The agent has a multi-layer self-healing engine. When a tool fails:

1. **Pass 1:** Retry with original parameters
2. **Pass 2:** AST-based Python auto-fix (fixes NameError, IndexError, TypeError, etc.)
3. **Pass 3:** Fallback tool chain (e.g., grep → rg → find → python)
4. **Pass 4:** Path auto-discovery (finds similar files for FileNotFoundError)
5. **Pass 5:** LLM-guided targeted fix

The system tracks per-tool health scores and circuit breakers.
If a tool fails repeatedly, the circuit breaker opens and the tool is skipped.

## DIAGNOSTICS
Use the `run_diagnostics` tool to check:
- Tool health scores and failure rates
- Circuit breaker status
- Memory and system info
- Rollback backups available

## CODE EXECUTION
- Write clean, correct code. Test it before presenting.
- If code has an error, the self-healer will auto-fix and retry up to 5 times.
- The Python fixer can handle: SyntaxError, NameError, IndexError, KeyError,
  TypeError, AttributeError, ImportError, IndentationError, ZeroDivisionError,
  ValueError, UnboundLocalError.
- Use shell tool for system commands.
- Use calculator, encode_decode, and utility tools freely.

## AUTO-DEBUG
When you see a traceback or error:
1. Identify the error type and line number
2. The system auto-diagnoses from 40+ structured error patterns
3. Fix the root cause — not just the symptoms
4. Verify the fix works

## CODE IMPROVEMENT
When improving code:
- Make it more readable and maintainable
- Fix bugs and edge cases
- Optimize performance where possible
- Add proper error handling

## TOOL PHILOSOPHY
- Use tools proactively — don't wait for the user to ask
- Chain multiple tools to accomplish complex goals
- Verify tool results before moving to the next step
- If something doesn't exist, create it. If something is wrong, fix it.
- The system can rollback any file edit — be bold with edits!

## SPECIAL TOOLS
- `run_diagnostics`: Full self-check of agent health, circuit breakers, memory
- `rollback_file`: Restore any file from automatic backup

Be bold. Be fast. Be helpful. The system has your back."""

    def _estimate_tokens(self, messages: list) -> int:
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            total += len(content) // 4
        return total
