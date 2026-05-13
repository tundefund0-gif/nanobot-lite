"""CLI commands for nanobot-lite."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import questionary
import typer
from loguru import logger

from nanobot_lite import __logo__, __version__
from nanobot_lite.agent.loop import AgentLoop
from nanobot_lite.agent.memory import SessionStore
from nanobot_lite.bus.queue import MessageBus
from nanobot_lite.channels.telegram import TelegramChannel, handle_outbound
from nanobot_lite.config.schema import Config, get_default_config_path, load_config, save_config
from nanobot_lite.providers import AnthropicProvider
from nanobot_lite.tools.base import ToolRegistry
from nanobot_lite.tools.filesystem import create_filesystem_tools
from nanobot_lite.tools.shell import create_shell_tool
from nanobot_lite.tools.web import create_web_tools


# Configure logging
logger.remove()
_log_handler_id = logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <5}</level> | <level>{message}</level>",
    level="INFO",
)


app = typer.Typer(
    name="nanobot-lite",
    help="Nanobot-Lite: Ultra-lightweight AI agent for Telegram on Termux",
)


def setup_logging(config: Config) -> None:
    """Configure loguru from config."""
    if config.log.level:
        level_map = {
            "DEBUG": 10, "INFO": 20, "SUCCESS": 25,
            "WARNING": 30, "ERROR": 40, "CRITICAL": 50,
        }
        level = level_map.get(config.log.level.upper(), 20)
        logger.remove()
        logger.add(
            sys.stderr,
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <5}</level> | <level>{message}</level>",
            level=level,
        )


@app.command()
def run(
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Config file path"),
    telegram_only: bool = typer.Option(False, "--telegram-only", help="Only run Telegram channel"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging"),
) -> None:
    """
    Run nanobot-lite with Telegram and the agent loop.
    """
    if verbose:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")

    # Load config
    config = load_config(config_path)
    setup_logging(config)

    logger.info(f"Nanobot-Lite v{__version__}")

    # Check for API key
    if not config.telegram.enabled:
        logger.error("No channel enabled in config!")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY environment variable not set!")
        logger.info("Set it with: export ANTHROPIC_API_KEY=sk-ant-...")
        return

    if not config.telegram.bot_token:
        logger.error("Telegram bot token not set in config!")
        logger.info("Add your bot token to ~/.nanobot_lite/config.yaml")
        return

    # Print logo
    print(__logo__)

    async def main() -> None:
        # Setup
        bus = MessageBus()

        # Provider
        provider = AnthropicProvider(api_key=api_key, model=config.agent.model)

        # Tools
        registry = ToolRegistry()
        registry.register(create_shell_tool())
        for tool in create_filesystem_tools():
            registry.register(tool)
        for tool in create_web_tools():
            registry.register(tool)

        # Session store
        store = SessionStore(config.memory.session_dir)
        config.memory.session_dir.mkdir(parents=True, exist_ok=True)

        # Agent loop
        agent = AgentLoop(
            bus=bus,
            provider=provider,
            config=config,
            tool_registry=registry,
            session_store=store,
        )

        # Telegram channel
        telegram = TelegramChannel(config=config, bus=bus)

        # Outbound handler task
        outbound_task = asyncio.create_task(handle_outbound(bus, config))

        # Agent loop task
        agent_task = asyncio.create_task(agent.run())

        # Telegram task
        telegram_task = asyncio.create_task(telegram.start())

        logger.info("All systems started! Press Ctrl+C to stop.")

        try:
            # Wait for all tasks
            await asyncio.gather(
                agent_task,
                telegram_task,
                outbound_task,
            )
        except asyncio.CancelledError:
            logger.info("Shutting down...")
            agent.stop()
            outbound_task.cancel()
            agent_task.cancel()
            telegram_task.cancel()
            await asyncio.gather(outbound_task, agent_task, telegram_task, return_exceptions=True)
            logger.info("Goodbye!")

    asyncio.run(main())


@app.command()
def setup() -> None:
    """Interactive setup wizard."""
    print(__logo__)
    print(f"Nanobot-Lite Setup Wizard v{__version__}\n")

    # Anthropic API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        api_key = questionary.text(
            "Enter your Anthropic API Key:",
            style=questionary.Style([("password", "bold")]),
        ).ask()
        if not api_key:
            typer.echo("API key required. Set ANTHROPIC_API_KEY environment variable or enter during setup.")
            api_key = ""

    # Telegram bot token
    bot_token = questionary.text(
        "Enter your Telegram Bot Token (from @BotFather):",
        style=questionary.Style([("password", "bold")]),
    ).ask()

    # Agent name
    agent_name = questionary.text(
        "Agent name (default: Nanobot-Lite):",
        default="Nanobot-Lite",
    ).ask()

    # Workspace directory
    workspace = questionary.text(
        "Workspace directory (default: ~/nanobot_workspace):",
        default="~/nanobot_workspace",
    ).ask()

    # Build config
    from nanobot_lite.config.schema import AgentConfig, LogConfig, MemoryConfig, TelegramConfig, ToolsConfig
    from pathlib import Path

    config = Config(
        agent=AgentConfig(
            name=agent_name or "Nanobot-Lite",
            system_prompt=(
                f"You are {agent_name or 'Nanobot-Lite'}, a helpful AI assistant. "
                "You have access to tools: web search, shell commands, and file operations. "
                "Be concise, helpful, and safe."
            ),
        ),
        telegram=TelegramConfig(
            enabled=bool(bot_token),
            bot_token=bot_token or "",
        ),
        memory=MemoryConfig(
            session_dir=Path.home() / ".nanobot_lite" / "sessions",
        ),
        tools=ToolsConfig(
            workspace_dir=Path(workspace).expanduser() if workspace else Path.home() / "nanobot_workspace",
        ),
    )

    # Save
    config_path = get_default_config_path()
    save_config(config, config_path)

    print(f"\n✅ Configuration saved to: {config_path}")
    print(f"\n📝 Next steps:")
    print(f"  1. Create your Telegram bot: message @BotFather on Telegram")
    print(f"  2. Get your API key from: https://console.anthropic.com/")
    print(f"  3. Run: nanobot-lite run")
    print(f"\n  export ANTHROPIC_API_KEY={api_key[:10]}..." if api_key else "  export ANTHROPIC_API_KEY=your-key")


@app.command()
def session(
    list_sessions: bool = typer.Option(False, "--list", "-l", help="List all sessions"),
    stats: Optional[str] = typer.Option(None, "--stats", "-s", help="Show stats for session key"),
    delete: Optional[str] = typer.Option(None, "--delete", "-d", help="Delete a session"),
) -> None:
    """Manage chat sessions."""
    config = load_config()
    store = SessionStore(config.memory.session_dir)

    if list_sessions:
        sessions = store.list_sessions()
        if not sessions:
            typer.echo("No sessions found.")
            return
        typer.echo(f"Sessions ({len(sessions)}):\n")
        for s in sessions:
            typer.echo(f"  {s}")

    elif stats:
        s = store.get_stats(stats)
        if not s:
            typer.echo(f"Session not found: {stats}")
            return
        typer.echo(f"Session: {s['session_key']}")
        typer.echo(f"  Messages: {s['message_count']}")
        typer.echo(f"  Turns: {s['turn_count']}")
        typer.echo(f"  Est. tokens: {s['estimated_tokens']}")
        typer.echo(f"  Created: {s['created_at']}")
        typer.echo(f"  Updated: {s['updated_at']}")

    elif delete:
        if store.delete(delete):
            typer.echo(f"Deleted: {delete}")
        else:
            typer.echo(f"Session not found: {delete}")

    else:
        typer.echo("Use --list, --stats <key>, or --delete <key>")


@app.command()
def shell(
    command: str = typer.Argument(..., help="Shell command to execute"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", "-C", help="Working directory"),
    timeout: int = typer.Option(30, "--timeout", "-t", help="Timeout in seconds"),
) -> None:
    """Quick shell command execution (no agent loop)."""
    import asyncio
    from nanobot_lite.tools.base import ToolRegistry
    from nanobot_lite.tools.shell import exec_shell

    registry = ToolRegistry()
    config = load_config()
    registry.set_context(
        workspace=str(config.tools.workspace_dir),
        shell_enabled=True,
        restrict_to_workspace=False,
    )

    async def run():
        result = await exec_shell(command, cwd=str(cwd) if cwd else None, timeout=timeout)
        print(result.content)
        sys.exit(0 if result.success else 1)

    asyncio.run(run())


@app.command()
def version() -> None:
    """Show version information."""
    typer.echo(f"Nanobot-Lite v{__version__}")
    typer.echo(f"Python {sys.version.split()[0]}")


if __name__ == "__main__":
    app()
