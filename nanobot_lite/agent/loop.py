"""The core agent loop — coordinates everything."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot_lite.agent.memory import ContextBuilder, Session, SessionStore
from nanobot_lite.bus.events import InboundMessage, Message, OutboundMessage
from nanobot_lite.bus.queue import MessageBus
from nanobot_lite.config.schema import Config
from nanobot_lite.providers.base import LLMProvider
from nanobot_lite.tools.base import ToolRegistry


@dataclass
class AgentContext:
    """Context available during agent execution."""
    session_key: str
    user_id: str
    chat_id: str
    config: Config
    tool_registry: ToolRegistry


class AgentLoop:
    """
    The main agent loop.

    Consumes inbound messages from the message bus, processes them through
    the LLM with tool execution, and publishes responses back.
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        config: Config,
        tool_registry: ToolRegistry,
        session_store: SessionStore,
    ):
        self.bus = bus
        self.provider = provider
        self.config = config
        self.tool_registry = tool_registry
        self.session_store = session_store
        self._running = False

        # Set tool context
        self.tool_registry.set_context(
            workspace=str(config.tools.workspace_dir),
            shell_enabled=config.tools.shell_enabled,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            allowed_commands=config.tools.allowed_commands,
            blocked_commands=config.tools.blocked_commands,
            shell_timeout=config.tools.shell_timeout,
        )

        # Ensure workspace exists
        config.tools.workspace_dir.mkdir(parents=True, exist_ok=True)

    async def run(self) -> None:
        """Main loop — consume inbound messages and process them."""
        self._running = True
        logger.info("Agent loop started")

        while self._running:
            try:
                # Wait for next inbound message
                inbound: InboundMessage = await self.bus.consume_inbound()
                logger.info(f"Processing: {inbound}")

                try:
                    await self._process_message(inbound)
                except Exception as e:
                    logger.exception(f"Error processing message: {inbound}")
                    await self._send_error(inbound, str(e))

                self.bus.task_done()

            except asyncio.CancelledError:
                logger.info("Agent loop cancelled")
                break
            except Exception as e:
                logger.exception("Agent loop error")
                await asyncio.sleep(1)  # Back off on error

        logger.info("Agent loop stopped")

    async def _process_message(self, inbound: InboundMessage) -> None:
        """Process a single inbound message."""
        session_key = inbound.session_key

        # Load or create session
        session = self.session_store.load(session_key)
        if session is None:
            session = Session(session_key=session_key)
            logger.info(f"Created new session: {session_key}")

        # Add user message
        msg_content = inbound.message.content
        session.add_message(role="user", content=msg_content)

        # Build context
        ctx_builder = ContextBuilder(
            session=session,
            system_prompt=self.config.agent.system_prompt,
        )

        # Check if compaction needed
        if ctx_builder.needs_compaction():
            ctx_builder.compact(keep_recent=15)
            self.session_store.save(session)

        # Build LLM messages
        llm_messages = ctx_builder.build(prepend_system=True)

        # Tool schemas
        tools = self.tool_registry.get_schemas()

        # Run the conversation
        response_text, tool_results = await self._run_turn(
            session=session,
            llm_messages=llm_messages,
            tools=tools,
            max_turns=self.config.agent.max_turns,
        )

        # Strip think blocks from response
        from nanobot_lite.utils.helpers import strip_think
        response_text = strip_think(response_text)

        # Add assistant response to session
        session.add_message(role="assistant", content=response_text)

        # Save session
        self.session_store.save(session)

        # Send response
        outbound = OutboundMessage(
            session_key=session_key,
            chat_id=inbound.chat_id,
            content=response_text,
            reply_to=inbound.message_id if self.config.telegram.reply_to_incoming else None,
        )
        await self.bus.publish_outbound(outbound)

    async def _run_turn(
        self,
        session: Session,
        llm_messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_turns: int = 50,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Run a single agent turn: send to LLM, execute tools, repeat.

        Returns (final_text, all_tool_results).
        """
        from nanobot_lite.providers.base import Message as LLMMessage

        # Convert dicts to Message objects
        messages = [LLMMessage(role=m["role"], content=m["content"]) for m in llm_messages]

        all_tool_results = []
        response_text = ""

        for turn in range(max_turns):
            # Send to LLM
            response = await self.provider.chat(
                messages=messages,
                tools=tools if tools else None,
                model=self.config.agent.model,
                max_tokens=self.config.agent.max_tokens,
                temperature=self.config.agent.temperature,
            )

            response_text = response.content

            # No tool calls — we're done
            if not response.tool_calls:
                break

            # Process each tool call
            for tc in response.tool_calls:
                # Add assistant message with tool use
                messages.append(LLMMessage(
                    role="assistant",
                    content="",
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                ))

                # Execute tool
                tool_result = await self._execute_tool(tc.name, tc.arguments)
                all_tool_results.append(tool_result)

                # Add tool result as a message
                result_content = tool_result.get("content", "")
                if not tool_result.get("success", True):
                    result_content = f"Error: {result_content}"

                messages.append(LLMMessage(
                    role="user",
                    content=result_content,
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                ))

        return response_text, all_tool_results

    async def _execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool by name with the given arguments."""
        tool = self.tool_registry.get(name)
        if not tool:
            return {"content": f"Unknown tool: {name}", "success": False}

        try:
            result = await tool.handler(**arguments)

            # Convert ToolResult to dict if needed
            if hasattr(result, "to_dict"):
                return result.to_dict()
            return result if isinstance(result, dict) else {"content": str(result), "success": True}

        except TypeError as e:
            # Wrong arguments
            return {"content": f"Tool execution error: {e}", "success": False, "error": str(e)}
        except Exception as e:
            logger.exception(f"Tool {name} failed")
            return {"content": f"Tool error: {e}", "success": False, "error": str(e)}

    async def _send_error(self, inbound: InboundMessage, error: str) -> None:
        """Send an error message to the user."""
        outbound = OutboundMessage(
            session_key=inbound.session_key,
            chat_id=inbound.chat_id,
            content=f"⚠️ Sorry, something went wrong:\n\n`{error[:500]}`",
            reply_to=inbound.message_id if self.config.telegram.reply_to_incoming else None,
        )
        await self.bus.publish_outbound(outbound)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
