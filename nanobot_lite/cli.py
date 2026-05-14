"""Advanced CLI with setup wizard, personality builder, and export tools."""
from __future__ import annotations

import asyncio
import json
import os
import platform
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

import questionary
import typer
from loguru import logger

from nanobot_lite import __logo__, __version__
from nanobot_lite.agent.loop import AgentLoop, RateLimiter, StreamConfig
from nanobot_lite.agent.memory import SessionStore
from nanobot_lite.bus.queue import MessageBus
from nanobot_lite.channels.telegram import TelegramChannel, handle_outbound
from nanobot_lite.config.schema import (
    Config,
    AgentConfig,
    LogConfig,
    MemoryConfig,
    TelegramConfig,
    ToolsConfig,
    get_default_config_path,
    load_config,
    save_config,
    ensure_default_config,
)
from nanobot_lite.providers import AnthropicProvider
from nanobot_lite.tools.base import ToolRegistry
from nanobot_lite.tools.shell import create_shell_tool
from nanobot_lite.tools.filesystem import create_filesystem_tools
from nanobot_lite.tools.web import create_web_tools
from nanobot_lite.tools.advanced import create_advanced_tools
from nanobot_lite.tools.code_runner import CodeRunner as create_code_runner


# ─── Logging ─────────────────────────────────────────────────────────────────

logger.remove()
_log_id = logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO",
)


# ─── App ─────────────────────────────────────────────────────────────────────

app = typer.Typer(
    name="nanobot-lite",
    help="Nanobot-Lite: Advanced AI agent for Telegram on Termux",
)


# ─── Config helpers ──────────────────────────────────────────────────────────

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
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
            level=level,
        )


# ─── Main run command ──────────────────────────────────────────────────────────

@app.command()
def run(
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Config file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose (DEBUG) logging"),
    no_rate_limit: bool = typer.Option(False, "--no-rate-limit", help="Disable rate limiting"),
) -> None:
    """
    Run nanobot-lite — the full AI agent with Telegram and tools.
    """
    if verbose:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")

    config = load_config(config_path)
    setup_logging(config)

    logger.info(f"Nanobot-Lite v{__version__} starting...")

    # API key check
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set!")
        logger.info("Run: export ANTHROPIC_API_KEY=sk-ant-...")
        return

    if not config.telegram.bot_token:
        logger.error("Telegram bot token not set!")
        logger.info("Edit: ~/.nanobot_lite/config.yaml")
        return

    print(__logo__)
    print(f"  🤖 Agent: {config.agent.name}")
    print(f"  🧠 Model: {config.agent.model}")
    print(f"  📁 Workspace: {config.tools.workspace_dir}")
    print(f"  🔒 Shell: {'enabled' if config.tools.shell_enabled else 'disabled'}")
    print()

    async def main() -> None:
        # Create message bus
        bus = MessageBus()

        # Create LLM provider
        provider = AnthropicProvider(api_key=api_key, model=config.agent.model)

        # Create tool registry with all tools
        registry = ToolRegistry()
        registry.register(create_shell_tool())
        for tool in create_filesystem_tools():
            registry.register(tool)
        for tool in create_web_tools():
            registry.register(tool)
        for tool in create_advanced_tools():
            registry.register(tool)
        registry.register(create_code_runner())

        # Ensure workspace exists
        config.tools.workspace_dir.mkdir(parents=True, exist_ok=True)

        # Session store
        store = SessionStore(config.memory.session_dir)
        config.memory.session_dir.mkdir(parents=True, exist_ok=True)

        # Rate limiter (skip if disabled)
        rate_limiter = None if no_rate_limit else RateLimiter(
            msgs_per_min=20,
            tokens_per_min=100000,
            turns_per_hour=50,
        )

        # Stream config
        stream_config = StreamConfig(enabled=True, typing_interval=3.0, chunk_size=50)

        # Create agent loop
        agent = AgentLoop(
            bus=bus,
            provider=provider,
            config=config,
            tool_registry=registry,
            session_store=store,
            rate_limiter=rate_limiter,
            stream_config=stream_config,
        )

        # Create Telegram channel
        telegram = TelegramChannel(config=config, bus=bus)

        # Tasks
        outbound_task = asyncio.create_task(handle_outbound(bus, config))
        agent_task = asyncio.create_task(agent.run())
        telegram_task = asyncio.create_task(telegram.start())

        logger.info("🚀 All systems running! Press Ctrl+C to stop.")

        # Handle shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(agent, outbound_task, agent_task, telegram_task)))

        try:
            await asyncio.gather(agent_task, telegram_task, outbound_task)
        except asyncio.CancelledError:
            logger.info("Shutting down...")
            await shutdown(agent, outbound_task, agent_task, telegram_task)
        except Exception as e:
            logger.error(f"Error: {e}")

    async def shutdown(agent, *tasks) -> None:
        agent.stop()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Goodbye!")

    asyncio.run(main())


# ─── Setup wizard ─────────────────────────────────────────────────────────────

@app.command()
def setup(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing config"),
) -> None:
    """
    Interactive setup wizard — configure everything step by step.
    """
    print(__logo__)
    print(f"⚙️ Nanobot-Lite Setup Wizard v{__version__}\n")

    cfg_path = get_default_config_path()

    if cfg_path.exists() and not force:
        overwrite = questionary.confirm(
            "Config already exists. Overwrite?",
            default=False,
        ).ask()
        if not overwrite:
            typer.echo("Setup cancelled.")
            return

    # ── Step 1: API Key ─────────────────────────────────────────────────────
    typer.echo("\n🔑 Step 1/6 — Anthropic API Key")
    typer.echo("  Get yours at: https://console.anthropic.com/")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        api_key = questionary.text(
            "Enter your Anthropic API key (sk-ant-...):",
            style=questionary.Style([("password", "bold")]),
        ).ask()
    if not api_key:
        typer.echo("❌ API key required. Run 'nanobot-lite setup' again.")
        return

    # Validate API key
    typer.echo("  Validating API key...")
    try:
        import urllib.request, json
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps({"model":"claude-sonnet-4-20250514","messages":[{"role":"user","content":"ping"}],"max_tokens":10}).encode(),
            method="POST",
        )
        req.add_header("x-api-key", api_key)
        req.add_header("anthropic-version", "2023-06-01")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                typer.echo("  ✅ API key valid!")
            else:
                typer.echo(f"  ⚠️ API returned status {resp.status}")
    except Exception as e:
        typer.echo(f"  ⚠️ Could not validate: {e}")

    # ── Step 2: Telegram ─────────────────────────────────────────────────────
    typer.echo("\n📱 Step 2/6 — Telegram Bot")
    typer.echo("  1. Open Telegram → search @BotFather")
    typer.echo("  2. Send /newbot → follow the prompts")
    typer.echo("  3. Copy your bot token (e.g. 123456789:ABC...)")
    bot_token = questionary.text(
        "Enter your Telegram bot token:",
        style=questionary.Style([("password", "bold")]),
    ).ask()
    if not bot_token:
        typer.echo("❌ Bot token required.")
        return

    # Test bot token
    typer.echo("  Testing bot token...")
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                typer.echo(f"  ✅ Bot confirmed: @{data['result']['username']}")
            else:
                typer.echo(f"  ⚠️ Bot error: {data}")
    except Exception as e:
        typer.echo(f"  ⚠️ Could not verify: {e}")

    # Allowlist
    typer.echo("\n🔐 Step 3/6 — Access Control")
    allowlist = questionary.text(
        "Allowed user IDs (comma-separated, leave blank for open):",
        default="",
    ).ask()
    allowed_users = [u.strip() for u in allowlist.split(",") if u.strip()]

    # ── Step 4: Agent config ─────────────────────────────────────────────────
    typer.echo("\n🤖 Step 4/6 — Agent Configuration")

    agent_name = questionary.text(
        "Agent name (default: Nanobot-Lite):",
        default="Nanobot-Lite",
    ).ask() or "Nanobot-Lite"

    model = questionary.select(
        "Default model:",
        choices=[
            "claude-sonnet-4-20250514",
            "claude-3-5-sonnet-20241022",
            "claude-3-haiku-20240307",
        ],
        default="claude-sonnet-4-20250514",
    ).ask()

    max_tokens = questionary.text(
        "Max tokens per response (default: 4096):",
        default="4096",
    ).ask()
    max_tokens = int(max_tokens) if max_tokens.isdigit() else 4096

    temp = questionary.text(
        "Temperature (0.0-1.0, default: 0.7):",
        default="0.7",
    ).ask()
    temperature = float(temp) if temp.replace(".", "").isdigit() else 0.7

    # ── Step 5: Personality ─────────────────────────────────────────────────
    typer.echo("\n🎭 Step 5/6 — Personality")

    personalities = {
        "helpful": "A helpful, friendly assistant that explains things clearly.",
        "concise": "A concise assistant that gives short, direct answers.",
        "creative": "A creative assistant with a playful, imaginative style.",
        "technical": "A technical assistant that speaks precisely and in detail.",
        "custom": "I'll write my own system prompt.",
    }

    personality = questionary.select(
        "Choose personality:",
        choices=list(personalities.keys()),
    ).ask()

    if personality == "custom":
        custom_prompt = questionary.text(
            "Enter system prompt:",
            default=f"You are {agent_name}, a helpful AI assistant.",
        ).ask()
        system_prompt = custom_prompt
    else:
        system_prompt = personalities[personality]

    # ── Step 6: Workspace ───────────────────────────────────────────────────
    typer.echo("\n📁 Step 6/6 — Workspace")

    workspace = questionary.text(
        "Workspace directory (default: ~/nanobot_workspace):",
        default="~/nanobot_workspace",
    ).ask() or "~/nanobot_workspace"

    workspace_dir = Path(workspace).expanduser().resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    shell_enabled = questionary.confirm(
        "Enable shell command execution?",
        default=True,
    ).ask()

    # ── Build & Save ─────────────────────────────────────────────────────────
    config = Config(
        agent=AgentConfig(
            name=agent_name,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
            max_turns=50,
        ),
        telegram=TelegramConfig(
            enabled=True,
            bot_token=bot_token,
            allowed_users=allowed_users,
        ),
        memory=MemoryConfig(
            session_dir=Path.home() / ".nanobot_lite" / "sessions",
            max_session_messages=200,
        ),
        tools=ToolsConfig(
            workspace_dir=workspace_dir,
            shell_enabled=shell_enabled,
        ),
        log=LogConfig(level="INFO"),
    )

    save_config(config, cfg_path)

    typer.echo(f"\n✅ Setup complete!")
    typer.echo(f"   Config saved: {cfg_path}")
    typer.echo(f"\n   Next steps:")
    typer.echo(f"   1. Set API key: export ANTHROPIC_API_KEY={api_key[:15]}...")
    typer.echo(f"   2. Start bot:   nanobot-lite run")
    typer.echo(f"   3. Message your bot on Telegram!")


# ─── Session management ───────────────────────────────────────────────────────

@app.command()
def session(
    list_sessions: bool = typer.Option(False, "--list", "-l", help="List all sessions"),
    stats: Optional[str] = typer.Option(None, "--stats", "-s", help="Stats for session key"),
    delete: Optional[str] = typer.Option(None, "--delete", "-d", help="Delete a session"),
    export: Optional[str] = typer.Option(None, "--export", "-e", help="Export session to file"),
    clear_all: bool = typer.Option(False, "--clear-all", help="Clear all sessions"),
) -> None:
    """
    Manage chat sessions.
    """
    config = load_config()
    store = SessionStore(config.memory.session_dir)

    if clear_all:
        confirm = questionary.confirm("Delete ALL sessions?", default=False).ask()
        if confirm:
            for s in store.list_sessions():
                store.delete(s)
            typer.echo("✅ All sessions deleted.")
        return

    if list_sessions:
        sessions = store.list_sessions()
        if not sessions:
            typer.echo("No sessions found.")
            return
        typer.echo(f"Sessions ({len(sessions)}):\n")
        for s in sessions:
            size = store.get_stats(s)
            if size:
                typer.echo(f"  📁 {s}")
                typer.echo(f"     Messages: {size.get('message_count', '?')} | "
                           f"Turns: {size.get('turn_count', '?')} | "
                           f"Tokens: {size.get('estimated_tokens', '?')}")
            else:
                typer.echo(f"  📁 {s}")
        return

    if stats:
        s = store.get_stats(stats)
        if not s:
            typer.echo(f"Session not found: {stats}")
            return
        typer.echo(f"Session: {s['session_key']}")
        for k, v in s.items():
            typer.echo(f"  {k}: {v}")
        return

    if delete:
        if store.delete(delete):
            typer.echo(f"Deleted: {delete}")
        else:
            typer.echo(f"Not found: {delete}")
        return

    if export:
        session = store.load(export)
        if not session:
            typer.echo(f"Not found: {export}")
            return

        from datetime import datetime
        lines = [f"# Nanobot-Lite Chat Export", f"# Session: {export}", f"# Date: {datetime.now().isoformat()}", "",]
        for msg in session.messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            lines.append(f"**{role.upper()}**: {content}")
            lines.append("")

        output = "\n".join(lines)
        out_path = Path.home() / f"nanobot_export_{export[:8]}.txt"
        out_path.write_text(output)
        typer.echo(f"Exported to: {out_path}")
        return

    typer.echo("Use: --list, --stats <key>, --delete <key>, --export <key>, --clear-all")


# ─── Health check ─────────────────────────────────────────────────────────────

@app.command()
def health() -> None:
    """
    Run a health check — verify all systems are operational.
    """
    typer.echo("🏥 Nanobot-Lite Health Check\n")

    checks = []

    # Config
    try:
        config = load_config()
        checks.append(("Config", True, f"loaded from {get_default_config_path()}"))
    except Exception as e:
        checks.append(("Config", False, str(e)))

    # API key
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        checks.append(("ANTHROPIC_API_KEY", True, "set"))
    else:
        checks.append(("ANTHROPIC_API_KEY", False, "not set — run 'export ANTHROPIC_API_KEY=...'"))

    # Bot token
    if config and config.telegram.bot_token:
        checks.append(("Telegram bot token", True, "configured"))
    else:
        checks.append(("Telegram bot token", False, "not configured"))

    # Workspace
    if config:
        ws = config.tools.workspace_dir
        if ws.exists():
            checks.append(("Workspace", True, str(ws)))
        else:
            checks.append(("Workspace", False, f"not found: {ws}"))

    # Session dir
    if config:
        sd = config.memory.session_dir
        sd.mkdir(parents=True, exist_ok=True)
        checks.append(("Session dir", True, str(sd)))

    # Python version
    checks.append(("Python", True, sys.version.split()[0]))

    # Platform
    checks.append(("Platform", True, f"{platform.system()} {platform.machine()}"))

    # Print results
    all_ok = True
    for name, ok, detail in checks:
        status = "✅" if ok else "❌"
        typer.echo(f"  {status} {name}: {detail}")
        if not ok:
            all_ok = False

    typer.echo()
    if all_ok:
        typer.echo("✅ All systems operational!")
    else:
        typer.echo("⚠️ Some checks failed. Review above.")


# ─── Shell command ────────────────────────────────────────────────────────────

@app.command()
def shell(
    command: str = typer.Argument(..., help="Shell command to execute"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", "-C", help="Working directory"),
    timeout: int = typer.Option(30, "--timeout", "-t", help="Timeout in seconds"),
) -> None:
    """
    Execute a shell command directly (no agent).
    """
    config = load_config()
    ws = str(config.tools.workspace_dir)

    result = subprocess.run(
        ["/bin/sh", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else ws,
    )

    if result.stdout:
        typer.echo(result.stdout)
    if result.stderr and result.returncode != 0:
        typer.echo(f"❌ {result.stderr}", err=True)

    sys.exit(result.returncode)


# ─── Version ─────────────────────────────────────────────────────────────────

@app.command()
def version() -> None:
    """Show version and system info."""
    typer.echo(f"Nanobot-Lite v{__version__}")
    typer.echo(f"Python: {sys.version.split()[0]}")
    typer.echo(f"Platform: {platform.system()} {platform.machine()}")


# ─── Personality builder ──────────────────────────────────────────────────────

@app.command()
def persona() -> None:
    """
    Interactive personality builder — customize the agent's behavior.
    """
    print(__logo__)
    print("🎭 Personality Builder\n")

    name = questionary.text("Agent name:", default="Nanobot-Lite").ask() or "Nanobot-Lite"

    traits = questionary.checkbox(
        "Select personality traits:",
        choices=[
            "Friendly & warm",
            "Concise & direct",
            "Technical & precise",
            "Playful & creative",
            "Calm & patient",
            "Analytical & thorough",
            "Humorous",
            "Professional",
        ],
    ).ask()

    expertise = questionary.checkbox(
        "Areas of expertise:",
        choices=[
            "Web development",
            "System administration",
            "Data analysis",
            "Creative writing",
            "General knowledge",
            "Math & science",
            "Code review",
            "Research",
        ],
    ).ask()

    style = questionary.select(
        "Response style:",
        choices=["Short answers", "Medium explanations", "Detailed breakdowns"],
    ).ask()

    # Build system prompt
    trait_text = {
        "Friendly & warm": "friendly and warm",
        "Concise & direct": "concise and gives direct answers",
        "Technical & precise": "technical and precise",
        "Playful & creative": "playful and creative",
        "Calm & patient": "calm and patient",
        "Analytical & thorough": "analytical and thorough",
        "Humorous": "with a good sense of humor",
        "Professional": "professional",
    }

    expertise_text = {
        "Web development": "web development and APIs",
        "System administration": "system administration and DevOps",
        "Data analysis": "data analysis and visualization",
        "Creative writing": "creative writing and content",
        "General knowledge": "general knowledge and research",
        "Math & science": "math and science",
        "Code review": "code review and debugging",
        "Research": "research and investigation",
    }

    style_map = {"Short answers": "Keep answers short and to the point.",
                 "Medium explanations": "Give medium-length explanations with examples.",
                 "Detailed breakdowns": "Provide detailed, thorough breakdowns."}

    prompt = f"You are {name}, {', '.join(trait_text.get(t, t) for t in traits)}."
    prompt += f"\n\nExpertise: {', '.join(expertise_text.get(e, e) for e in expertise)}."
    prompt += f"\n\n{style_map.get(style, '')}"
    prompt += "\n\nAlways be helpful, safe, and honest."

    typer.echo("\n📝 Generated system prompt:\n")
    typer.echo(f"```\n{prompt}\n```\n")

    save = questionary.confirm("Save this as the agent's system prompt?", default=True).ask()
    if save:
        config = load_config()
        config.agent.system_prompt = prompt
        config.agent.name = name
        save_config(config, get_default_config_path())
        typer.echo("✅ Saved to config!")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()