"""
Telegram service for YTBot

Handles Telegram bot communication and message processing.
"""

import asyncio
from typing import Optional, Dict, Any, Callable, List
from telegram import Bot
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler
from telegram.error import Conflict

from ..core.config import CONFIG
from ..core.enhanced_logger import get_logger, log_function_entry_exit

logger = get_logger(__name__)


class TelegramService:
    """Telegram bot service for handling bot communication with unified connection management."""

    _instance: Optional['TelegramService'] = None
    _instance_lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.bot: Optional[Bot] = None
        self.application: Optional[Application] = None
        self.token = CONFIG['telegram']['token']
        self._connected = False
        self._handlers_registered = False
        self._command_handlers: List[tuple] = []
        self._message_handlers: List[tuple] = []
        self._callback_handlers: List[Callable] = []
        self._error_handlers: List[Callable] = []
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10
        self._reconnect_delay = 5  # seconds
        self._polling_started = False
        self._reconnect_lock = asyncio.Lock()
        self._connection_lock = asyncio.Lock()
        self._polling_lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        self._initialized = True
        self._external_polling = False  # 标记是否有外部管理polling

    @classmethod
    async def get_instance(cls) -> 'TelegramService':
        """Get or create the singleton instance."""
        async with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @log_function_entry_exit(logger)
    async def connect(self) -> bool:
        """Connect to Telegram servers with detailed logging."""
        async with self._connection_lock:
            if self._connected:
                logger.debug("Already connected to Telegram")
                return True

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

            except Conflict as ce:
                logger.error(f"❌ Conflict error during connection: {ce}")
                logger.error("Another bot instance is already running")
                self._connected = False
                return False
            except Exception as e:
                logger.error(f"❌ Failed to connect to Telegram: {e}")
                logger.exception("Telegram connection error details:")
                self._connected = False
                return False

    @log_function_entry_exit(logger)
    async def disconnect(self):
        """Disconnect from Telegram servers with detailed logging."""
        async with self._connection_lock:
            logger.info("📴 Starting Telegram disconnection...")

            try:
                if self.application:
                    logger.info("🔄 Disconnecting from Telegram servers...")
                    app_state = getattr(self.application, 'running', 'unknown')
                    logger.debug(f"Application state: {app_state}")

                    # Stop polling if it was started
                    if self._polling_started:
                        try:
                            logger.debug("Stopping updater polling...")
                            if self.application.updater and self.application.updater.running:
                                await self.application.updater.stop()
                                logger.debug("Updater polling stopped")
                        except Exception as e:
                            logger.warning(f"⚠️ Error stopping updater: {e}")

                        try:
                            logger.debug("Stopping application...")
                            await self.application.stop()
                            logger.debug("Application stopped")
                        except Exception as e:
                            logger.warning(f"⚠️ Error stopping application: {e}")

                        try:
                            logger.debug("Shutting down application...")
                            await self.application.shutdown()
                            logger.debug("Application shutdown complete")
                        except Exception as e:
                            logger.warning(f"⚠️ Error shutting down application: {e}")

                        self._polling_started = False

                # Clear references
                self.application = None
                self.bot = None
                self._connected = False
                self._external_polling = False
                self._shutdown_event.set()

                logger.info("✅ Disconnected from Telegram servers")

            except Exception as e:
                logger.error(f"❌ Error disconnecting from Telegram: {e}")
                logger.exception("Disconnection error details:")

    @log_function_entry_exit(logger)
    async def reconnect(self, start_polling: bool = False) -> bool:
        """
        Reconnect to Telegram servers with automatic retry logic.
        Handles Conflict errors to prevent multiple bot instances.

        Args:
            start_polling: Whether to start polling after reconnection.
                          Should be False if external code manages polling.

        Returns:
            bool: True if reconnection successful, False otherwise
        """
        # Use lock to prevent concurrent reconnection attempts
        async with self._reconnect_lock:
            logger.info(f"🔄 Starting Telegram reconnection... (start_polling={start_polling})")

            # Disconnect first if still connected
            if self._connected or self.application:
                logger.info("📴 Disconnecting existing connection before reconnect...")
                await self.disconnect()
                # Wait longer to ensure previous connection is fully terminated
                await asyncio.sleep(5)

            # Attempt reconnection with exponential backoff
            for attempt in range(1, self._max_reconnect_attempts + 1):
                self._reconnect_attempts = attempt
                logger.info(f"🔄 Reconnection attempt {attempt}/{self._max_reconnect_attempts}")

                try:
                    # Try to connect
                    success = await self.connect()

                    if success and self.application:
                        logger.info("✅ Reconnection successful, re-registering handlers...")

                        # Re-register all handlers
                        await self._reregister_handlers()

                        # Only start polling if requested and not already started
                        if start_polling and not self._polling_started:
                            try:
                                async with self._polling_lock:
                                    if not self._polling_started:  # Double-check
                                        await self.application.initialize()
                                        await self.application.start()
                                        await self.application.updater.start_polling()
                                        self._polling_started = True
                                        logger.info("✅ Telegram polling started after reconnection")
                            except Conflict as ce:
                                logger.warning(f"⚠️ Conflict error during polling start: {ce}")
                                logger.info(
                                    "⏳ Another bot instance may be running, waiting before retry..."
                                )
                                # Force disconnect and wait longer
                                await self.disconnect()
                                await asyncio.sleep(10)
                                continue
                        else:
                            logger.info("⏭️  Skipping polling start (managed externally)")

                        logger.info("✅ Telegram reconnection complete")
                        self._reconnect_attempts = 0
                        return True

                except Conflict as ce:
                    logger.warning(f"⚠️ Conflict error on attempt {attempt}: {ce}")
                    logger.info(
                        "⏳ Another bot instance may be running, "
                        "waiting before retry..."
                    )
                    await self.disconnect()
                    await asyncio.sleep(10)
                    continue
                except Exception as e:
                    logger.error(f"❌ Reconnection attempt {attempt} failed: {e}")

                # Wait before next attempt (exponential backoff with jitter)
                delay = min(self._reconnect_delay * (2 ** (attempt - 1)), 60)
                # Add jitter to prevent thundering herd
                import random
                delay = delay + random.uniform(0, 2)
                logger.info(f"⏳ Waiting {delay:.1f} seconds before next reconnection attempt...")
                await asyncio.sleep(delay)

            logger.error(f"❌ Failed to reconnect after {self._max_reconnect_attempts} attempts")
            return False

    async def _reregister_handlers(self):
        """Re-register all previously registered handlers after reconnection."""
        if not self.application:
            logger.error("❌ Cannot re-register handlers: application not initialized")
            return

        logger.info("📝 Re-registering handlers...")

        # Re-register command handlers
        for command, callback in self._command_handlers:
            try:
                self.application.add_handler(CommandHandler(command, callback))
                logger.debug(f"✅ Re-registered command handler: /{command}")
            except Exception as e:
                logger.error(f"❌ Failed to re-register command handler /{command}: {e}")

        # Re-register message handlers
        for filters, callback in self._message_handlers:
            try:
                self.application.add_handler(MessageHandler(filters, callback))
                logger.debug("✅ Re-registered message handler")
            except Exception as e:
                logger.error(f"❌ Failed to re-register message handler: {e}")

        # Re-register callback handlers
        for callback in self._callback_handlers:
            try:
                self.application.add_handler(CallbackQueryHandler(callback))
                logger.debug("✅ Re-registered callback handler")
            except Exception as e:
                logger.error(f"❌ Failed to re-register callback handler: {e}")

        # Re-register error handlers
        for callback in self._error_handlers:
            try:
                self.application.add_error_handler(callback)
                logger.debug("✅ Re-registered error handler")
            except Exception as e:
                logger.error(f"❌ Failed to re-register error handler: {e}")

        logger.info(
            f"✅ Handlers re-reregistered: {len(self._command_handlers)} commands, "
            f"{len(self._message_handlers)} message handlers, "
            f"{len(self._callback_handlers)} callback handlers, "
            f"{len(self._error_handlers)} error handlers"
        )
        self._handlers_registered = True

    async def check_connection_health(self) -> bool:
        """
        Check if the Telegram connection is healthy.

        Returns:
            bool: True if connection is healthy, False otherwise
        """
        if not self.connected or not self.bot:
            return False

        try:
            # Try to get bot info as a health check
            bot_info = await self.bot.get_me()
            return bot_info is not None
        except Conflict:
            logger.warning("Conflict error during health check - another instance running")
            return False
        except Exception as e:
            logger.debug(f"Connection health check failed: {e}")
            return False

    @property
    def connected(self) -> bool:
        """Check if connected to Telegram"""
        return self._connected and self.bot is not None

    @property
    def is_polling(self) -> bool:
        """Check if polling is active"""
        return self._polling_started

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

        # Store handler for reconnection
        self._command_handlers.append((command, callback))

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

        # Store handler for reconnection
        self._message_handlers.append((filters, callback))

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

        # Store handler for reconnection
        self._callback_handlers.append(callback)

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

        # Store handler for reconnection
        self._error_handlers.append(callback)

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
        async with self._polling_lock:
            if self._polling_started:
                logger.warning("⚠️ Polling already started, skipping")
                return

            if not self.application:
                logger.error("❌ Telegram application not initialized")
                return

            try:
                logger.info("🚀 Starting Telegram polling...")
                await self.application.initialize()
                await self.application.start()
                await self.application.updater.start_polling()
                self._polling_started = True
                self._external_polling = True  # Mark that polling is managed externally
                logger.info("✅ Telegram polling started successfully")
            except Conflict as ce:
                logger.error(f"❌ Conflict error starting polling: {ce}")
                logger.error(
                    "Another bot instance may be running. "
                    "Please ensure only one instance is active."
                )
                self._polling_started = False
                raise
            except Exception as e:
                logger.error(f"❌ Failed to start Telegram polling: {e}")
                logger.exception("Polling error details:")
                self._polling_started = False

    @log_function_entry_exit(logger)
    async def stop_polling(self):
        """Stop polling for updates with detailed logging"""
        async with self._polling_lock:
            logger.info("🛑 Stopping Telegram polling...")

            if not self.application:
                logger.debug("No Telegram application to stop polling")
                return

            try:
                logger.debug("Stopping updater...")
                if self.application.updater and self.application.updater.running:
                    await self.application.updater.stop()
                    logger.debug("Updater stopped")

                logger.debug("Calling application.stop()...")
                await self.application.stop()

                logger.debug("Shutting down application...")
                await self.application.shutdown()

                self._polling_started = False
                self._external_polling = False
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
