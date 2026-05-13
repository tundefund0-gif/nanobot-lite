"""Telegram channel implementation."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot_lite.bus.events import InboundMessage, Message, OutboundMessage
from nanobot_lite.bus.queue import MessageBus
from nanobot_lite.config.schema import Config


# Maximum message length for Telegram
MAX_MESSAGE_LENGTH = 4000


class TelegramChannel:
    """Telegram bot channel using python-telegram-bot."""

    name = "telegram"
    display_name = "Telegram"

    def __init__(self, config: Config, bus: MessageBus):
        self.config = config
        self.telegram_config = config.telegram
        self.bus = bus
        self._app = None
        self._running = False
        self.logger = logger.bind(channel=self.name)

    async def start(self) -> None:
        """Start the Telegram bot."""
        if not self.telegram_config.enabled:
            self.logger.info("Telegram channel disabled")
            return

        if not self.telegram_config.bot_token:
            self.logger.warning("Telegram bot token not set — channel disabled")
            return

        self.logger.info("Starting Telegram channel...")

        try:
            from telegram import BotCommand
            from telegram.ext import (
                Application,
                CommandHandler,
                MessageHandler,
                filters,
                CallbackContext,
            )
        except ImportError:
            self.logger.error("python-telegram-bot not installed. Run: pip install python-telegram-bot")
            return

        # Build the application
        app = Application.builder().token(self.telegram_config.bot_token).build()

        # Register handlers
        app.add_handler(CommandHandler("start", self._on_start))
        app.add_handler(CommandHandler("help", self._on_help))
        app.add_handler(CommandHandler("reset", self._on_reset))
        app.add_handler(CommandHandler("stats", self._on_stats))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        app.add_handler(MessageHandler(filters.PHOTO, self._on_photo))
        app.add_handler(MessageHandler(filters.VOICE, self._on_voice))

        # Set bot commands
        await app.initialize()
        await app.bot.set_my_commands([
            BotCommand("start", "Start the bot"),
            BotCommand("help", "Show help"),
            BotCommand("reset", "Reset conversation"),
            BotCommand("stats", "Show session stats"),
        ])

        self._app = app
        self._running = True

        self.logger.info("Telegram channel started")
        await app.run_polling(drop_pending_updates=True)

    async def stop(self) -> None:
        """Stop the Telegram bot."""
        self._running = False
        if self._app:
            await self._app.stop()
            self.logger.info("Telegram channel stopped")

    # --- Command Handlers ---

    async def _on_start(self, update: Any, context: CallbackContext) -> None:
        await update.message.reply_text(
            "👋 *Nanobot-Lite* is online!\n\n"
            "I'm an AI assistant that can help you with tasks using tools like "
            "web search, shell commands, and file operations.\n\n"
            "Just send me a message and I'll get to work!",
            parse_mode="markdown",
        )

    async def _on_help(self, update: Any, context: CallbackContext) -> None:
        await update.message.reply_text(
            "*Available Commands:*\n\n"
            "/reset — Reset your conversation\n"
            "/stats — Show session statistics\n"
            "\n"
            "_Just send any message to chat with me!_",
            parse_mode="markdown",
        )

    async def _on_reset(self, update: Any, context: CallbackContext) -> None:
        """Reset the conversation by deleting the session."""
        from nanobot_lite.agent.memory import SessionStore

        session_key = self._get_session_key(update.effective_user.id, update.effective_chat.id)
        store = SessionStore(self.config.memory.session_dir)

        if store.delete(session_key):
            await update.message.reply_text("✅ Conversation reset! Starting fresh.")
        else:
            await update.message.reply_text("Started a new conversation.")

    async def _on_stats(self, update: Any, context: CallbackContext) -> None:
        """Show session statistics."""
        from nanobot_lite.agent.memory import SessionStore

        session_key = self._get_session_key(update.effective_user.id, update.effective_chat.id)
        store = SessionStore(self.config.memory.session_dir)
        stats = store.get_stats(session_key)

        if not stats:
            await update.message.reply_text("No conversation yet. Say hello!")
            return

        await update.message.reply_text(
            f"*Session Stats:*\n\n"
            f"Messages: {stats['message_count']}\n"
            f"Turns: {stats['turn_count']}\n"
            f"Est. tokens: {stats['estimated_tokens']}\n"
            f"Last active: {stats['updated_at']}",
            parse_mode="markdown",
        )

    async def _on_message(self, update: Any, context: CallbackContext) -> None:
        """Handle a text message."""
        # Check user access
        if not self._check_access(update):
            return

        text = update.message.text or ""
        if not text.strip():
            return

        session_key = self._get_session_key(update.effective_user.id, update.effective_chat.id)

        inbound = InboundMessage(
            session_key=session_key,
            user_id=str(update.effective_user.id),
            chat_id=str(update.effective_chat.id),
            message=Message(role="user", content=text),
            message_id=update.message.message_id,
            reply_to=update.message.reply_to_message_id if self.telegram_config.reply_to_incoming else None,
        )

        await self.bus.publish_inbound(inbound)

        # Send "processing" indicator
        await update.message.reply_text("🤔 processing...", quote=False)

    async def _on_photo(self, update: Any, context: CallbackContext) -> None:
        """Handle a photo — forward as a text description."""
        if not self._check_access(update):
            return

        # For now, just acknowledge
        await update.message.reply_text(
            "📷 Image received. For now, images are described but not processed. "
            "I'll let you know when image analysis is ready!",
        )

    async def _on_voice(self, update: Any, context: CallbackContext) -> None:
        """Handle a voice message — try to transcribe."""
        if not self._check_access(update):
            return

        await update.message.reply_text(
            "🎤 Voice message received. Transcription isn't set up yet — "
            "please send text messages for now.",
        )

    def _check_access(self, update: Any) -> bool:
        """Check if the user is allowed to use the bot."""
        user_id = str(update.effective_user.id)

        # Admin always has access
        if self.telegram_config.admin_user_id and user_id == self.telegram_config.admin_user_id:
            return True

        # If allowed_users is set, check against it
        if self.telegram_config.allowed_users:
            if user_id not in self.telegram_config.allowed_users:
                self.logger.warning(f"Unauthorized user: {user_id}")
                update.message.reply_text("⛔ You're not authorized to use this bot.")
                return False

        return True

    def _get_session_key(self, user_id: int, chat_id: int) -> str:
        """Generate a session key for a user/chat pair."""
        return f"telegram:{chat_id}:{user_id}"


# --- Outbound handler ---
async def handle_outbound(bus: MessageBus, config: Config) -> None:
    """
    Consume outbound messages from the bus and send them to Telegram.
    Runs as a separate async task.
    """
    if not config.telegram.enabled or not config.telegram.bot_token:
        return

    try:
        from telegram import Bot
    except ImportError:
        return

    bot = Bot(token=config.telegram.bot_token)

    while True:
        try:
            outbound = await bus.consume_outbound()
            chat_id = int(outbound.chat_id)

            content = outbound.content

            # Split long messages
            parts = _split_message(content)

            for i, part in enumerate(parts):
                reply_to = outbound.reply_to if i == 0 else None
                try:
                    if outbound.reply_to and outbound.message_id:
                        # Edit existing message
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=outbound.message_id,
                            text=part,
                            parse_mode="HTML" if outbound.parse_mode == "html" else None,
                        )
                    else:
                        # Send new message
                        await bot.send_message(
                            chat_id=chat_id,
                            text=part,
                            reply_to_message_id=reply_to,
                            parse_mode="Markdown",
                        )
                except Exception as e:
                    logger.error(f"Failed to send message: {e}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Outbound handler error: {e}")
            await asyncio.sleep(1)


def _split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split a long message into chunks."""
    if len(text) <= max_len:
        return [text]

    parts = []
    lines = text.split("\n")
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            if current:
                parts.append(current.strip())
            current = line
        else:
            current = (current + "\n" + line) if current else line

    if current:
        parts.append(current.strip())

    return parts
