"""Advanced Telegram channel with slash commands, streaming, and rich UI."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

from nanobot_lite.bus.events import InboundMessage, Message, OutboundMessage
from nanobot_lite.bus.queue import MessageBus
from nanobot_lite.config.schema import Config


# ─── Slash command states ─────────────────────────────────────────────────────

(
    STATE_WAITING_CMD,
    STATE_SHELL_CMD,
    STATE_SEARCH_QUERY,
    STATE_CONFIG_EDIT,
) = range(4)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _extract_command(text: str) -> tuple[str | None, str | None]:
    """Extract /command and arguments from text."""
    text = text.strip()
    if not text.startswith("/"):
        return None, None
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else None
    return cmd, args


def _format_time(seconds: float) -> str:
    """Format duration in human-readable form."""
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    return f"{seconds/3600:.1f}h"


# ─── Conversation handler keyboard ────────────────────────────────────────────

def _main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📊 Stats", callback_data="cmd_stats"),
         InlineKeyboardButton("💾 Sessions", callback_data="cmd_sessions")],
        [InlineKeyboardButton("🔧 Config", callback_data="cmd_config"),
         InlineKeyboardButton("🗑️ Clear Chat", callback_data="cmd_clear")],
        [InlineKeyboardButton("📤 Export", callback_data="cmd_export"),
         InlineKeyboardButton("⏱️ Uptime", callback_data="cmd_uptime")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ─── Telegram Channel ─────────────────────────────────────────────────────────

class TelegramChannel:
    """
    Advanced Telegram channel with:
    - Slash commands (/help, /search, /shell, /session, /stats, etc.)
    - Streaming typing indicators
    - Inline keyboard menus
    - Rich formatted responses
    - User allowlisting
    - Message editing support
    """

    def __init__(self, config: Config, bus: MessageBus):
        self.config = config
        self.bus = bus
        self._app: Application | None = None
        self._start_time = datetime.now()
        self._user_stats: dict[str, dict] = {}
        self._streaming_tasks: dict[int, asyncio.Task] = {}

    async def start(self) -> None:
        """Start the Telegram bot."""
        token = self.config.telegram.bot_token
        if not token:
            logger.error("No Telegram bot token configured!")
            return

        logger.info("Starting Telegram bot...")
        self._app = Application.builder().token(token).build()

        # Register command handlers
        self._register_commands()

        # Register message handler (non-command messages)
        self._app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_text_message,
        ))

        # Register callback query handler (inline buttons)
        self._app.add_handler(MessageHandler(
            filters.UpdateType.CALLBACK_QUERY,
            self._handle_callback,
        ))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram bot started!")

    async def stop(self) -> None:
        """Stop the Telegram bot gracefully."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram bot stopped")

    def _register_commands(self) -> None:
        """Register all slash commands."""
        app = self._app
        if not app:
            return

        handlers = [
            CommandHandler("help", self._cmd_help),
            CommandHandler("start", self._cmd_start),
            CommandHandler("stats", self._cmd_stats),
            CommandHandler("sessions", self._cmd_sessions),
            CommandHandler("clear", self._cmd_clear),
            CommandHandler("export", self._cmd_export),
            CommandHandler("uptime", self._cmd_uptime),
            CommandHandler("shell", self._cmd_shell),
            CommandHandler("search", self._cmd_search),
            CommandHandler("sysinfo", self._cmd_sysinfo),
            CommandHandler("id", self._cmd_id),
            CommandHandler("menu", self._cmd_menu),
            CommandHandler("ping", self._cmd_ping),
            CommandHandler("version", self._cmd_version),
            CommandHandler("config", self._cmd_config),
        ]

        for h in handlers:
            app.add_handler(h)

    # ─── Message routing ─────────────────────────────────────────────────────

    async def _handle_text_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle non-command text messages — route to agent."""
        if not update.message:
            return

        user_id = str(update.message.from_user.id)
        chat_id = update.message.chat_id

        # Check allowlist
        if self.config.telegram.allowed_users and user_id not in self.config.telegram.allowed_users:
            await update.message.reply_text(
                "⛔ Access denied. You are not on the allowlist.",
                reply_to_message_id=update.message.message_id,
            )
            return

        # Track stats
        self._track_user(user_id)

        text = update.message.text.strip()

        # Check for inline commands
        cmd, args = _extract_command(text)
        if cmd:
            return  # Already handled by CommandHandler

        # Send to agent
        inbound = InboundMessage(
            platform="telegram",
            user_id=user_id,
            chat_id=str(chat_id),
            text=text,
            message_id=str(update.message.message_id),
            username=update.message.from_user.username or "",
            first_name=update.message.from_user.first_name or "",
        )
        await self.bus.inbound.put(inbound)

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline button callbacks."""
        query = update.callback_query
        if not query:
            return
        await query.answer()

        data = query.data or ""
        user_id = str(query.from_user.id)

        if data == "cmd_stats":
            await self._cmd_stats(update, ctx)
        elif data == "cmd_sessions":
            await self._cmd_sessions(update, ctx)
        elif data == "cmd_clear":
            await self._cmd_clear(update, ctx)
        elif data == "cmd_export":
            await self._cmd_export(update, ctx)
        elif data == "cmd_uptime":
            await self._cmd_uptime(update, ctx)
        elif data == "cmd_menu":
            await self._cmd_menu(update, ctx)
        elif data == "cmd_config":
            await self._cmd_config(update, ctx)

    def _track_user(self, user_id: str) -> None:
        """Track user message stats."""
        if user_id not in self._user_stats:
            self._user_stats[user_id] = {"messages": 0, "first_seen": datetime.now().isoformat()}
        self._user_stats[user_id]["messages"] += 1

    # ─── Slash Commands ──────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not update.message:
            return

        user = update.message.from_user
        welcome = f"👋 Hey {user.first_name}! I'm *Nanobot-Lite* — your AI assistant on your phone.\n\n"
        welcome += "Type a message and I'll think, search the web, run commands, and more.\n\n"
        welcome += "_I'm an advanced agent with access to multiple tools._\n\n"
        welcome += "Use /help to see all commands."

        await update.message.reply_text(
            welcome,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_main_menu_keyboard(),
        )

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command — show all commands."""
        if not update.message:
            return

        help_text = """
🤖 *Nanobot-Lite Commands*

*Agent Commands*
`/search <query>` — Web search
`/shell <cmd>` — Run shell command
`/sysinfo` — System information

*Session Management*
`/stats` — Bot statistics
`/sessions` — Active sessions
`/clear` — Clear conversation
`/export` — Export chat history

*Utility*
`/ping` — Health check
`/uptime` — Bot uptime
`/id` — Your user ID
`/menu` — Show menu
`/version` — Version info
`/config` — View config

*Just type* any message and I'll respond!
"""
        await update.message.reply_text(
            help_text.strip(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_main_menu_keyboard(),
        )

    async def _cmd_stats(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /stats command."""
        if not update.message:
            return

        # Try to get agent stats from bus
        agent_stats = {}
        try:
            # Stats are tracked in the agent loop
            pass
        except:
            pass

        uptime = (datetime.now() - self._start_time).total_seconds()

        stats_text = f"""
📊 *Nanobot-Lite Statistics*

🕐 Uptime: {_format_time(uptime)}
👥 Users tracked: {len(self._user_stats)}
💬 Messages processed: {sum(u['messages'] for u in self._user_stats.values())}

*Top Users:*
"""
        sorted_users = sorted(self._user_stats.items(), key=lambda x: x[1]["messages"], reverse=True)
        for uid, data in sorted_users[:3]:
            stats_text += f"\n  User `{uid[-6:]}`: {data['messages']} msgs"

        await update.message.reply_text(
            stats_text.strip(),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_sessions(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /sessions command."""
        if not update.message:
            return

        from nanobot_lite.agent.memory import SessionStore
        store = SessionStore(self.config.memory.session_dir)
        sessions = store.list_sessions()

        if not sessions:
            await update.message.reply_text("No active sessions.")
            return

        text = f"📁 *Sessions ({len(sessions)}):*\n\n"
        for s in sessions[:20]:
            text += f"  ▸ `{s}`\n"

        if len(sessions) > 20:
            text += f"\n  _...and {len(sessions)-20} more_"

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_clear(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /clear command — clear user session."""
        if not update.message:
            return

        user_id = str(update.message.from_user.id)
        from nanobot_lite.agent.memory import SessionStore
        store = SessionStore(self.config.memory.session_dir)

        # Find and delete this user's session
        sessions = store.list_sessions()
        for s in sessions:
            if user_id in s:
                store.delete(s)
                await update.message.reply_text(
                    "🗑️ Session cleared! Starting fresh.",
                    reply_markup=_main_menu_keyboard(),
                )
                return

        await update.message.reply_text(
            "✅ Already fresh — no session to clear.",
            reply_markup=_main_menu_keyboard(),
        )

    async def _cmd_export(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /export command."""
        if not update.message:
            return

        user_id = str(update.message.from_user.id)
        from nanobot_lite.agent.memory import SessionStore
        store = SessionStore(self.config.memory.session_dir)
        sessions = store.list_sessions()

        # Find user session
        for s in sessions:
            if user_id in s:
                session = store.load(s)
                if session:
                    # Build export text
                    export = f"📤 Chat export — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                    for msg in session.messages:
                        role = msg.get("role", "?")
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                        prefix = "👤" if role == "user" else "🤖"
                        export += f"{prefix} *{role.upper()}*: {content[:300]}\n\n"

                    await update.message.reply_text(
                        export[:4000],
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    return

        await update.message.reply_text("No session to export.")

    async def _cmd_uptime(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /uptime command."""
        if not update.message:
            return

        uptime = datetime.now() - self._start_time
        total_s = uptime.total_seconds()
        days = int(total_s // 86400)
        hours = int((total_s % 86400) // 3600)
        mins = int((total_s % 3600) // 60)

        await update.message.reply_text(
            f"⏱️ Bot uptime: {days}d {hours}h {mins}m\nStarted: {self._start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_shell(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /shell command — direct shell execution."""
        if not update.message:
            return

        args = ctx.args
        if not args:
            await update.message.reply_text(
                "Usage: /shell <command>\nExample: /shell ls -la ~/",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        cmd = " ".join(args)
        code, out, err = self._run_shell(cmd, timeout=30)

        if code == 0:
            output = out[:3000] or "(no output)"
        else:
            output = f"❌ Exit code: {code}\n{err or out[:3000]}"

        await update.message.reply_text(
            f"```bash\n$ {cmd}\n```\n{output}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )

    async def _cmd_search(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /search command — direct web search."""
        if not update.message:
            return

        if not ctx.args:
            await update.message.reply_text("Usage: /search <query>")
            return

        query = " ".join(ctx.args)
        from nanobot_lite.utils.helpers import web_search

        await update.message.reply_text("🔍 Searching...")
        results = web_search(query, num_results=5)

        if results and "error" not in results[0]:
            text = f"🔍 *Results for:* `{query}`\n\n"
            for i, r in enumerate(results, 1):
                text += f"{i}. [{r['title']}]({r['url']})\n"
                if r.get("snippet"):
                    text += f"   _{r['snippet'][:150]}_...\n\n"
            await update.message.reply_text(
                text.strip(),
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        else:
            await update.message.reply_text(f"❌ No results or error: {results[0].get('error', 'Unknown')}")

    async def _cmd_sysinfo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /sysinfo command."""
        if not update.message:
            return

        import platform, os, subprocess

        info = "🖥️ *System Info*\n\n"
        info += f"OS: {platform.system()} {platform.release()}\n"
        info += f"Machine: {platform.machine()}\n"
        info += f"Python: {platform.python_version()}\n"

        cpu_count = os.cpu_count() or 1
        info += f"CPU cores: {cpu_count}\n"

        try:
            with open("/proc/meminfo") as f:
                meminfo = f.read()
            total_kb = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1))
            avail_kb = int(re.search(r"MemAvailable:\s+(\d+)", meminfo).group(1))
            used_pct = (total_kb - avail_kb) / total_kb * 100
            info += f"Memory: {used_pct:.0f}% used ({avail_kb//1024}MB available)"
        except:
            pass

        await update.message.reply_text(info, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_id(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /id command — show user and chat IDs."""
        if not update.message:
            return

        user = update.message.from_user
        chat = update.message.chat

        text = f"👤 *Your Info*\n\n"
        text += f"User ID: `{user.id}`\n"
        if user.username:
            text += f"Username: @{user.username}\n"
        text += f"First name: {user.first_name}\n"
        text += f"\n💬 *Chat Info*\n\n"
        text += f"Chat ID: `{chat.id}`\n"
        text += f"Chat type: {chat.type}"

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_menu(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /menu command."""
        if not update.message:
            return

        await update.message.reply_text(
            "📋 *Main Menu*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_main_menu_keyboard(),
        )

    async def _cmd_ping(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ping command — health check."""
        if not update.message:
            return

        start = datetime.now()
        await update.message.reply_text("🏓 Pong!")
        latency = (datetime.now() - start).total_seconds() * 1000
        await update.message.edit_message_text(
            text=f"🏓 Pong! Latency: {latency:.0f}ms",
        )

    async def _cmd_version(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /version command."""
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
        """Handle /config command — show current config."""
        if not update.message:
            return

        text = "⚙️ *Current Config*\n\n"
        text += f"Agent name: `{self.config.agent.name}`\n"
        text += f"Model: `{self.config.agent.model}`\n"
        text += f"Max tokens: `{self.config.agent.max_tokens}`\n"
        text += f"Temp: `{self.config.agent.temperature}`\n"
        text += f"Max turns: `{self.config.agent.max_turns}`\n"
        text += f"Workspace: `{self.config.tools.workspace_dir}`\n"
        text += f"Shell enabled: `{self.config.tools.shell_enabled}`"

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    def _run_shell(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        """Run shell command synchronously."""
        try:
            result = subprocess.run(
                ["/bin/sh", "-c", cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Timed out after {timeout}s"
        except Exception as e:
            return -1, "", str(e)


# ─── Outbound handler ───────────────────────────────────────────────────────

async def handle_outbound(bus: MessageBus, config: Config) -> None:
    """Process outbound messages from the agent and send to Telegram."""
    from telegram import Bot

    bot_token = config.telegram.bot_token
    if not bot_token:
        return

    bot = Bot(token=bot_token)

    while True:
        try:
            outbound = await bus.outbound.get()

            if isinstance(outbound, OutboundMessage):
                if outbound.action == "typing":
                    try:
                        await bot.send_chat_action(chat_id=outbound.chat_id, action="typing")
                    except:
                        pass
                elif outbound.text:
                    try:
                        await bot.send_message(
                            chat_id=outbound.chat_id,
                            text=outbound.text[:4096],
                            parse_mode=ParseMode.MARKDOWN,
                            reply_to_message_id=int(outbound.reply_to) if outbound.reply_to else None,
                        )
                    except Exception as e:
                        logger.error(f"Failed to send message: {e}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Outbound handler error: {e}")
            await asyncio.sleep(1)