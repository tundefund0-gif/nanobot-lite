"""Advanced Telegram channel with auto-execute, inline code results, and rich UI."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from nanobot_lite.bus.events import InboundMessage, OutboundMessage
from nanobot_lite.bus.queue import MessageBus
from nanobot_lite.config.schema import Config


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _format_time(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    return f"{seconds/3600:.1f}h"


def _extract_command(text: str) -> tuple[str | None, str | None]:
    text = text.strip()
    if not text.startswith("/"):
        return None, None
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else None
    return cmd, args


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📊 Stats", callback_data="cmd_stats"),
         InlineKeyboardButton("💾 Sessions", callback_data="cmd_sessions")],
        [InlineKeyboardButton("🔧 Config", callback_data="cmd_config"),
         InlineKeyboardButton("🗑️ Clear", callback_data="cmd_clear")],
        [InlineKeyboardButton("📤 Export", callback_data="cmd_export"),
         InlineKeyboardButton("⏱️ Uptime", callback_data="cmd_uptime")],
        [InlineKeyboardButton("🖥️ Run", callback_data="cmd_run"),
         InlineKeyboardButton("🔍 Search", callback_data="cmd_search")],
    ]
    return InlineKeyboardMarkup(keyboard)


def _escape_markdown_v2(text: str) -> str:
    """Escape special chars for MarkdownV2."""
    # This is handled by the telegram library, keep for reference
    return text


# ─── Telegram Channel ─────────────────────────────────────────────────────────

class TelegramChannel:
    """
    Advanced Telegram channel with:
    - Auto-execute on all messages
    - Slash commands with inline feedback
    - Rich formatted responses
    - User allowlisting
    - Streaming typing indicators
    - Code execution results inline
    """

    def __init__(self, config: Config, bus: MessageBus):
        self.config = config
        self.bus = bus
        self._app: Application | None = None
        self._start_time = datetime.now()
        self._user_stats: dict[str, dict] = {}
        self._pending_edits: dict[int, int] = {}  # message_id -> edit_count

    async def start(self) -> None:
        token = self.config.telegram.bot_token
        if not token:
            logger.error("No Telegram bot token!")
            return

        logger.info("Starting advanced Telegram channel...")
        self._app = Application.builder().token(token).build()
        self._register_commands()

        # All text messages go to agent (auto-execute)
        self._app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_text_message,
        ))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram channel started!")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    def _register_commands(self) -> None:
        app = self._app
        if not app:
            return

        commands = [
            ("help", self._cmd_help, "Show all commands"),
            ("start", self._cmd_start, "Welcome message"),
            ("stats", self._cmd_stats, "Bot statistics"),
            ("sessions", self._cmd_sessions, "List sessions"),
            ("clear", self._cmd_clear, "Clear your session"),
            ("export", self._cmd_export, "Export chat history"),
            ("uptime", self._cmd_uptime, "Bot uptime"),
            ("shell", self._cmd_shell, "Run shell command"),
            ("search", self._cmd_search, "Web search"),
            ("sysinfo", self._cmd_sysinfo, "System info"),
            ("id", self._cmd_id, "Your user ID"),
            ("menu", self._cmd_menu, "Show menu"),
            ("ping", self._cmd_ping, "Health check"),
            ("version", self._cmd_version, "Version info"),
            ("config", self._cmd_config, "Show config"),
            ("run", self._cmd_run, "Run code"),
            ("health", self._cmd_health, "Health check"),
            ("stop", self._cmd_stop, "Stop bot"),
            ("exec", self._cmd_exec, "Quick code execution"),
        ]

        for name, handler, desc in commands:
            app.add_handler(CommandHandler(name, handler))

    # ─── Message routing ─────────────────────────────────────────────────────

    async def _handle_text_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Route non-command messages to the agent for auto-execution."""
        if not update.message:
            return

        user_id = str(update.message.from_user.id)

        # Allowlist check
        if self.config.telegram.allowed_users and user_id not in self.config.telegram.allowed_users:
            await update.message.reply_text("⛔ Access denied.", reply_to_message_id=update.message.message_id)
            return

        # Track stats
        self._track_user(user_id)

        text = update.message.text.strip()
        if not text:
            return

        # Check if it's a slash command (already handled by CommandHandler)
        cmd, _ = _extract_command(text)
        if cmd:
            return

        # Show thinking indicator
        status_msg = await update.message.reply_text("🤖 Thinking...")

        # Route to agent
        inbound = InboundMessage(
            platform="telegram",
            user_id=user_id,
            chat_id=str(update.message.chat_id),
            text=text,
            message_id=str(update.message.message_id),
            username=update.message.from_user.username or "",
            first_name=update.message.from_user.first_name or "",
        )
        await self.bus.inbound.put(inbound)

    def _track_user(self, user_id: str) -> None:
        if user_id not in self._user_stats:
            self._user_stats[user_id] = {"messages": 0, "first_seen": datetime.now().isoformat()}
        self._user_stats[user_id]["messages"] += 1

    # ─── Slash Commands ──────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        user = update.message.from_user
        await update.message.reply_text(
            f"👋 Hey {user.first_name}! I'm *Nanobot-Lite* — fully autonomous AI agent.\n\n"
            f"Type anything and I'll execute it automatically. No approvals needed.\n\n"
            f"Use /help for all commands.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_main_menu_keyboard(),
        )

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            "*🤖 Nanobot-Lite Commands*\n\n"
            "*Agent*\n"
            "`/search <q>` — Web search\n"
            "`/run <code>` — Execute code\n"
            "`/exec <code>` — Quick code (inline)\n"
            "`/shell <cmd>` — Shell command\n\n"
            "*Management*\n"
            "`/stats` — Bot stats\n"
            "`/sessions` — List sessions\n"
            "`/clear` — Clear chat\n"
            "`/export` — Export history\n"
            "`/health` — Health check\n\n"
            "*Info*\n"
            "`/sysinfo` — System info\n"
            "`/uptime` — Uptime\n"
            "`/id` — Your ID\n"
            "`/menu` — Menu\n"
            "`/version` — Version\n"
            "`/config` — Config\n\n"
            "*Or just type anything — I auto-execute!*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_main_menu_keyboard(),
        )

    async def _cmd_stats(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        uptime = (datetime.now() - self._start_time).total_seconds()
        total_msgs = sum(u["messages"] for u in self._user_stats.values())
        await update.message.reply_text(
            f"📊 *Stats*\n\n"
            f"🕐 Uptime: {_format_time(uptime)}\n"
            f"👥 Users: {len(self._user_stats)}\n"
            f"💬 Messages: {total_msgs}",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_sessions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        from nanobot_lite.agent.memory import SessionStore
        store = SessionStore(self.config.memory.session_dir)
        sessions = store.list_sessions()
        if not sessions:
            await update.message.reply_text("No sessions.")
            return
        text = f"*Sessions ({len(sessions)}):*\n\n"
        for s in sessions[:20]:
            text += f"▸ `{s}`\n"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

    async def _cmd_clear(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        user_id = str(update.message.from_user.id)
        from nanobot_lite.agent.memory import SessionStore
        store = SessionStore(self.config.memory.session_dir)
        for s in store.list_sessions():
            if user_id in s:
                store.delete(s)
        await update.message.reply_text("🗑️ Session cleared!", reply_markup=_main_menu_keyboard())

    async def _cmd_export(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        user_id = str(update.message.from_user.id)
        from nanobot_lite.agent.memory import SessionStore
        store = SessionStore(self.config.memory.session_dir)
        for s in store.list_sessions():
            if user_id in s:
                session = store.load(s)
                if session:
                    lines = [f"# Export — {datetime.now().isoformat()}", ""]
                    for msg in session.messages:
                        role = msg.get("role", "?")
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                        lines.append(f"**{role.upper()}**: {content[:500]}")
                        lines.append("")
                    out = "\n".join(lines)
                    path = Path.home() / f"export_{s[:8]}.txt"
                    path.write_text(out)
                    await update.message.reply_text(f"📤 Exported to: `{path}`", parse_mode=ParseMode.MARKDOWN)
                    return
        await update.message.reply_text("No session to export.")

    async def _cmd_uptime(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        uptime = datetime.now() - self._start_time
        total = uptime.total_seconds()
        d, h, m = int(total // 86400), int((total % 86400) // 3600), int((total % 3600) // 60)
        await update.message.reply_text(
            f"⏱️ Uptime: {d}d {h}h {m}m\nStarted: {self._start_time.strftime('%Y-%m-%d %H:%M')}",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_shell(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        if not ctx.args:
            await update.message.reply_text("Usage: `/shell <command>`", parse_mode=ParseMode.MARKDOWN)
            return
        import subprocess
        cmd = " ".join(ctx.args)
        await update.message.reply_text(f"⚡ Executing: `{cmd}`")
        try:
            r = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=30)
            out = r.stdout or r.stderr or "(no output)"
            status = "🟢" if r.returncode == 0 else f"🔴 (exit {r.returncode})"
            await update.message.reply_text(f"{status}\n```\n{out[:3000]}\n```", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")

    async def _cmd_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        if not ctx.args:
            await update.message.reply_text("Usage: `/search <query>`")
            return
        query = " ".join(ctx.args)
        from nanobot_lite.utils.helpers import web_search
        await update.message.reply_text("🔍 Searching...")
        results = web_search(query, num_results=5)
        if results and "error" not in results[0]:
            text = f"🔍 *{query}*\n\n"
            for i, r in enumerate(results, 1):
                text += f"{i}. [{r['title']}]({r['url']})\n  _{r.get('snippet', '')[:150]}_\n\n"
            await update.message.reply_text(text.strip(), parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        else:
            await update.message.reply_text(f"❌ {results[0].get('error', 'No results')}")

    async def _cmd_sysinfo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        import platform, os
        info = (
            f"🖥️ *System Info*\n\n"
            f"OS: {platform.system()} {platform.release()}\n"
            f"Machine: {platform.machine()}\n"
            f"Python: {platform.python_version()}\n"
            f"Cores: {os.cpu_count() or '?'}\n"
        )
        try:
            with open("/proc/meminfo") as f:
                m = f.read()
            total = int(re.search(r"MemTotal:\s+(\d+)", m).group(1))
            avail = int(re.search(r"MemAvailable:\s+(\d+)", m).group(1))
            used_pct = (total - avail) / total * 100
            info += f"RAM: {used_pct:.0f}% used ({avail//1024}MB free)"
        except:
            pass
        await update.message.reply_text(info, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_id(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        u = update.message.from_user
        await update.message.reply_text(
            f"👤 *ID Info*\n\n"
            f"User: `{u.id}`\n"
            f"Username: @{u.username or 'none'}\n"
            f"Name: {u.first_name}\n"
            f"Chat: `{update.message.chat_id}`",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_menu(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text("📋 *Menu*", parse_mode=ParseMode.MARKDOWN, reply_markup=_main_menu_keyboard())

    async def _cmd_ping(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        start = datetime.now()
        msg = await update.message.reply_text("🏓")
        lat = (datetime.now() - start).total_seconds() * 1000
        await msg.edit_text(f"🏓 Pong! `{lat:.0f}ms`", parse_mode=ParseMode.MARKDOWN)

    async def _cmd_version(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        import sys, nanobot_lite
        await update.message.reply_text(
            f"📦 *Nanobot-Lite*\n\n"
            f"Version: `{nanobot_lite.__version__}`\n"
            f"Python: `{sys.version.split()[0]}`\n"
            f"Platform: `{sys.platform}`",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_config(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            f"⚙️ *Config*\n\n"
            f"Name: `{self.config.agent.name}`\n"
            f"Model: `{self.config.agent.model}`\n"
            f"Tokens: `{self.config.agent.max_tokens}`\n"
            f"Temp: `{self.config.agent.temperature}`\n"
            f"Shell: `{self.config.tools.shell_enabled}`\n"
            f"Workspace: `{self.config.tools.workspace_dir}`",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_run(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Run code directly."""
        if not update.message:
            return
        if not ctx.args:
            await update.message.reply_text("Usage: `/run <code>`\nExample: `/run print('hello')`")
            return
        code = " ".join(ctx.args)
        await update.message.reply_text("⚡ Running code...")
        from nanobot_lite.tools.code_runner import CodeRunner
        runner = CodeRunner(workspace_dir=self.config.tools.workspace_dir, timeout=30)
        result = await runner.execute({"code": code, "language": "auto"})
        await update.message.reply_text(
            result.content[:4096],
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_exec(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Quick code execution inline."""
        if not update.message:
            return
        if not ctx.args:
            await update.message.reply_text("Usage: `/exec <code>`")
            return
        code = " ".join(ctx.args)
        from nanobot_lite.tools.code_runner import CodeRunner
        runner = CodeRunner(workspace_dir=self.config.tools.workspace_dir, timeout=30)
        result = await runner.execute({"code": code, "language": "auto"})
        await update.message.reply_text(result.content[:4096], parse_mode=ParseMode.MARKDOWN)

    async def _cmd_health(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        checks = []
        # Config
        try:
            from nanobot_lite.config.schema import load_config, get_default_config_path
            c = load_config()
            checks.append(("Config", True, str(get_default_config_path())))
        except Exception as e:
            checks.append(("Config", False, str(e)))
        # API key
        import os
        if os.environ.get("ANTHROPIC_API_KEY"):
            checks.append(("ANTHROPIC_API_KEY", True, "set"))
        else:
            checks.append(("ANTHROPIC_API_KEY", False, "not set"))
        # Bot token
        if self.config.telegram.bot_token:
            checks.append(("Bot token", True, "configured"))
        else:
            checks.append(("Bot token", False, "missing"))
        # Workspace
        ws = self.config.tools.workspace_dir
        if ws.exists():
            checks.append(("Workspace", True, str(ws)))
        else:
            checks.append(("Workspace", False, f"not found"))

        text = "🏥 *Health Check*\n\n"
        all_ok = True
        for name, ok, detail in checks:
            text += f"{'✅' if ok else '❌'} {name}: {detail}\n"
            if not ok:
                all_ok = False
        text += f"\n{'✅ All OK' if all_ok else '⚠️ Issues found'}"
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text("🛑 Stopping bot... Goodbye!")
        import os, signal
        os.kill(os.getpid(), signal.SIGINT)


# ─── Outbound handler ───────────────────────────────────────────────────────

async def handle_outbound(bus: MessageBus, config: Config) -> None:
    """Send outbound messages to Telegram."""
    from telegram import Bot

    bot_token = config.telegram.bot_token
    if not bot_token:
        return

    bot = Bot(token=bot_token)

    while True:
        try:
            outbound = await asyncio.wait_for(bus.outbound.get(), timeout=30.0)

            if isinstance(outbound, OutboundMessage):
                if outbound.action == "typing":
                    try:
                        await asyncio.wait_for(
                            bot.send_chat_action(chat_id=outbound.chat_id, action="typing"),
                            timeout=10.0,
                        )
                    except asyncio.TimeoutError:
                        pass  # silently ignore typing indicator timeouts
                    except Exception:
                        pass
                elif outbound.text:
                    try:
                        await asyncio.wait_for(
                            bot.send_message(
                                chat_id=outbound.chat_id,
                                text=outbound.text[:4096],
                                parse_mode=ParseMode.MARKDOWN,
                                reply_to_message_id=int(outbound.reply_to) if outbound.reply_to else None,
                            ),
                            timeout=30.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Telegram send timed out after 30s for chat {outbound.chat_id}")
                    except Exception as e:
                        logger.warning(f"Telegram send error: {e}")

        except asyncio.TimeoutError:
            # No messages in 30s — loop continues
            continue
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Outbound error: {e}")
            await asyncio.sleep(1)