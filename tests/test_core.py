"""Tests for nanobot-lite."""
import pytest


def test_config_schema():
    """Test that config schema loads correctly."""
    from nanobot_lite.config.schema import Config
    cfg = Config()
    assert cfg.agent.name == "Nanobot-Lite"
    assert cfg.telegram.enabled == True
    assert cfg.tools.shell_enabled == True


def test_tool_registry():
    """Test tool registry basics."""
    from nanobot_lite.tools.base import ToolRegistry, Tool

    registry = ToolRegistry()

    async def dummy_handler(**kwargs):
        return {"content": "ok", "success": True}

    tool = Tool(
        name="test",
        description="A test tool",
        input_schema={"type": "object", "properties": {}},
        handler=dummy_handler,
    )

    registry.register(tool)
    assert "test" in registry
    assert len(registry) == 1
    assert registry.get("test").name == "test"


def test_message_bus():
    """Test message bus queue operations."""
    import asyncio
    from nanobot_lite.bus.queue import MessageBus
    from nanobot_lite.bus.events import InboundMessage, Message

    bus = MessageBus()
    assert bus.inbound.qsize() == 0

    # Check that queues exist
    assert hasattr(bus, "inbound")
    assert hasattr(bus, "outbound")
    assert hasattr(bus, "tool_calls")
    assert hasattr(bus, "tool_results")


def test_session_memory():
    """Test session store round-trip."""
    import tempfile
    import shutil
    from pathlib import Path
    from nanobot_lite.agent.memory import Session, SessionStore

    tmp = Path(tempfile.mkdtemp())
    try:
        store = SessionStore(tmp)

        session = Session(session_key="test:123:456")
        session.add_message("user", "Hello")
        session.add_message("assistant", "Hi there!")

        store.save(session)

        loaded = store.load("test:123:456")
        assert loaded is not None
        assert len(loaded.messages) == 2
        assert loaded.messages[0].content == "Hello"

    finally:
        shutil.rmtree(tmp)


def test_strip_think():
    """Test think block stripping."""
    from nanobot_lite.utils.helpers import strip_think

    text = "<think> This is reasoning </think> Hello world"
    result = strip_think(text)
    assert "Hello world" in result
    assert "<think>" not in result
    assert "</think>" not in result


def test_truncate_text():
    """Test text truncation."""
    from nanobot_lite.utils.helpers import truncate_text

    long_text = "a" * 5000
    result = truncate_text(long_text, max_chars=100)
    assert len(result) == 100
    assert result.endswith("...")


def test_estimate_tokens():
    """Test token estimation."""
    from nanobot_lite.utils.helpers import estimate_tokens

    text = "Hello world this is a test"
    tokens = estimate_tokens(text)
    assert tokens > 0
    assert tokens < len(text)


def test_tools_validate():
    """Test tool argument validation."""
    from nanobot_lite.tools.base import Tool

    async def handler(cmd: str):
        pass

    tool = Tool(
        name="shell",
        description="Run a command",
        input_schema={
            "type": "object",
            "properties": {"cmd": {"type": "string"}},
            "required": ["cmd"],
        },
        handler=handler,
    )

    valid, msg = tool.validate_arguments({"cmd": "ls"})
    assert valid

    valid, msg = tool.validate_arguments({})
    assert not valid
    assert "Missing" in msg
