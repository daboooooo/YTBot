"""
Telegram service for YTBot

Handles Telegram bot communication and message processing.
"""

from typing import Optional, Dict, Any
from telegram import Bot
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler

from ..core.config import CONFIG
from ..core.enhanced_logger import get_logger, log_function_entry_exit

logger = get_logger(__name__)


class TelegramService:
    """Telegram bot service for handling bot communication"""

    def __init__(self):
        self.bot: Optional[Bot] = None
        self.application: Optional[Application] = None
        self.token = CONFIG['telegram']['token']
        self._connected = False

    @log_function_entry_exit(logger)
    async def connect(self) -> bool:
        """Connect to Telegram servers with detailed logging"""
        logger.info("📱 Starting Telegram connection...")
        logger.debug(f"Bot token present: {bool(self.token)}")

        try:
            if not self.token:
                logger.error("❌ Telegram bot token not configured")
                return False

            logger.info("🔗 Connecting to Telegram servers...")
            logger.debug(f"Token length: {len(self.token)} characters")

            self.application = Application.builder().token(self.token).build()
            self.bot = self.application.bot

            # Test connection
            logger.debug("Testing connection with get_me()...")
            bot_info = await self.bot.get_me()
            logger.info(f"✅ Connected to Telegram: @{bot_info.username}")
            logger.info(f"🤖 Bot ID: {bot_info.id}")
            logger.info(f"📛 Bot Name: {bot_info.first_name}")

            self._connected = True
            return True

        except Exception as e:
            logger.error(f"❌ Failed to connect to Telegram: {e}")
            logger.exception("Telegram connection error details:")
            self._connected = False
            return False

    @log_function_entry_exit(logger)
    async def disconnect(self):
        """Disconnect from Telegram servers with detailed logging"""
        logger.info("📴 Starting Telegram disconnection...")

        try:
            if self.application:
                logger.info("🔄 Disconnecting from Telegram servers...")
                app_state = getattr(self.application, 'running', 'unknown')
                logger.debug(f"Application state: {app_state}")

                # Telegram doesn't require explicit disconnection
                self.application = None
                self.bot = None
                self._connected = False

                logger.info("✅ Disconnected from Telegram servers")
            else:
                logger.debug("No active Telegram connection to disconnect")

        except Exception as e:
            logger.error(f"❌ Error disconnecting from Telegram: {e}")
            logger.exception("Disconnection error details:")

    @property
    def connected(self) -> bool:
        """Check if connected to Telegram"""
        return self._connected and self.bot is not None

    @log_function_entry_exit(logger)
    async def send_message(
        self,
        chat_id: int,
        text: str,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """Send a message to a chat with detailed logging"""
        logger.info(f"📤 Sending message to chat {chat_id}")
        logger.debug(f"Message text: {text[:50]}..." if len(text) > 50 else f"Message text: {text}")
        logger.debug(f"Additional kwargs: {list(kwargs.keys())}")

        if not self.connected:
            logger.error("❌ Not connected to Telegram")
            return None

        try:
            logger.debug(f"Calling bot.send_message with chat_id={chat_id}")
            message = await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                **kwargs
            )

            if message:
                logger.info(f"✅ Message sent successfully to chat {chat_id}")
                logger.debug(f"Message ID: {message.message_id}")
                return message.to_dict()
            else:
                logger.warning(f"⚠️  No message returned for chat {chat_id}")
                return None

        except Exception as e:
            logger.error(f"❌ Failed to send message to {chat_id}: {e}")
            logger.exception("Message sending error details:")
            return None

    @log_function_entry_exit(logger)
    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """Edit an existing message with detailed logging"""
        logger.info(f"✏️  Editing message {message_id} in chat {chat_id}")
        logger.debug(f"New text: {text[:50]}..." if len(text) > 50 else f"New text: {text}")

        if not self.connected:
            logger.error("❌ Not connected to Telegram")
            return None

        try:
            logger.debug(f"Calling bot.edit_message_text with message_id={message_id}")
            message = await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                **kwargs
            )

            if message:
                logger.info(f"✅ Message {message_id} edited successfully in chat {chat_id}")
                return message.to_dict()
            else:
                logger.warning(f"⚠️  No message returned for edit operation in chat {chat_id}")
                return None

        except Exception as e:
            error_str = str(e)
            if "Message is not modified" in error_str:
                logger.debug(f"Message {message_id} content unchanged, skipping")
                return {"message_id": message_id, "unchanged": True}
            logger.error(f"❌ Failed to edit message {message_id} in {chat_id}: {e}")
            logger.exception("Message editing error details:")
            return None

    @log_function_entry_exit(logger)
    async def get_bot_info(self) -> Optional[Dict[str, Any]]:
        """Get information about the bot with detailed logging"""
        logger.info("ℹ️  Getting bot information...")

        if not self.connected:
            logger.error("❌ Not connected to Telegram")
            return None

        try:
            logger.debug("Calling bot.get_me()...")
            bot_info = await self.bot.get_me()

            if bot_info:
                logger.info(f"✅ Bot info retrieved: @{bot_info.username}")
                logger.debug(f"Bot details: ID={bot_info.id}, Name={bot_info.first_name}")
                return bot_info.to_dict()
            else:
                logger.warning("⚠️  No bot info returned")
                return None

        except Exception as e:
            logger.error(f"❌ Failed to get bot info: {e}")
            logger.exception("Bot info retrieval error details:")
            return None

    @log_function_entry_exit(logger)
    def add_command_handler(self, command: str, callback):
        """Add a command handler with detailed logging"""
        logger.info(f"➕ Adding command handler: /{command}")
        func_name = callback.__name__ if hasattr(callback, '__name__') else str(callback)
        logger.debug(f"Callback function: {func_name}")

        if not self.application:
            logger.error("❌ Telegram application not initialized")
            return

        try:
            self.application.add_handler(CommandHandler(command, callback))
            logger.info(f"✅ Command handler /{command} added successfully")
        except Exception as e:
            logger.error(f"❌ Failed to add command handler /{command}: {e}")
            logger.exception("Command handler error details:")

    @log_function_entry_exit(logger)
    def add_message_handler(self, filters, callback):
        """Add a message handler with detailed logging"""
        logger.info("➕ Adding message handler")
        logger.debug(f"Filters: {filters}")
        func_name = callback.__name__ if hasattr(callback, '__name__') else str(callback)
        logger.debug(f"Callback function: {func_name}")

        if not self.application:
            logger.error("❌ Telegram application not initialized")
            return

        try:
            self.application.add_handler(MessageHandler(filters, callback))
            logger.info("✅ Message handler added successfully")
        except Exception as e:
            logger.error(f"❌ Failed to add message handler: {e}")
            logger.exception("Message handler error details:")

    @log_function_entry_exit(logger)
    def add_callback_handler(self, callback):
        """Add a callback query handler with detailed logging"""
        logger.info("➕ Adding callback handler")
        func_name = callback.__name__ if hasattr(callback, '__name__') else str(callback)
        logger.debug(f"Callback function: {func_name}")

        if not self.application:
            logger.error("❌ Telegram application not initialized")
            return

        try:
            self.application.add_handler(CallbackQueryHandler(callback))
            logger.info("✅ Callback handler added successfully")
        except Exception as e:
            logger.error(f"❌ Failed to add callback handler: {e}")
            logger.exception("Callback handler error details:")

    @log_function_entry_exit(logger)
    def add_error_handler(self, callback):
        """Add an error handler with detailed logging"""
        logger.info("➕ Adding error handler")
        func_name = callback.__name__ if hasattr(callback, '__name__') else str(callback)
        logger.debug(f"Error handler function: {func_name}")

        if not self.application:
            logger.error("❌ Telegram application not initialized")
            return

        try:
            self.application.add_error_handler(callback)
            logger.info("✅ Error handler added successfully")
        except Exception as e:
            logger.error(f"❌ Failed to add error handler: {e}")
            logger.exception("Error handler error details:")

    @log_function_entry_exit(logger)
    async def start_polling(self):
        """Start polling for updates with detailed logging"""
        logger.info("🔄 Starting Telegram polling...")

        if not self.application:
            logger.error("❌ Telegram application not initialized")
            return

        try:
            logger.info("🚀 Starting Telegram polling loop...")
            await self.application.run_polling()
            logger.info("✅ Telegram polling started successfully")
        except Exception as e:
            logger.error(f"❌ Failed to start Telegram polling: {e}")
            logger.exception("Polling error details:")

    @log_function_entry_exit(logger)
    async def stop_polling(self):
        """Stop polling for updates with detailed logging"""
        logger.info("🛑 Stopping Telegram polling...")

        if not self.application:
            logger.debug("No Telegram application to stop polling")
            return

        try:
            logger.debug("Calling application.stop()...")
            await self.application.stop()
            logger.info("✅ Telegram polling stopped successfully")
        except Exception as e:
            logger.error(f"❌ Failed to stop Telegram polling: {e}")
            logger.exception("Stop polling error details:")

    @log_function_entry_exit(logger)
    def check_user_permission(self, chat_id: int) -> bool:
        """Check if user has permission to use the bot with detailed logging"""
        logger.debug(f"🔍 Checking permission for chat ID: {chat_id}")

        allowed_ids = CONFIG['telegram']['allowed_chat_ids']
        admin_id = CONFIG['telegram'].get('admin_chat_id', '')

        logger.debug(f"Allowed IDs: {allowed_ids}")
        logger.debug(f"Admin ID: {admin_id}")

        # Check if chat_id is in allowed list or is admin
        is_allowed = str(chat_id) in allowed_ids
        is_admin = str(chat_id) == admin_id
        has_permission = is_allowed or is_admin

        if has_permission:
            logger.info(f"✅ Permission granted for chat ID: {chat_id}")
            if is_admin:
                logger.debug(f"Chat {chat_id} is admin")
        else:
            logger.warning(f"❌ Permission denied for chat ID: {chat_id}")

        return has_permission