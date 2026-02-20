"""
Main CLI entry point for YTBot
"""

import asyncio
import argparse
import sys
from typing import Dict, Any, Optional

from ytbot.core.config import CONFIG, validate_config
from ytbot.core.enhanced_logger import get_logger, setup_exception_handler, log_function_entry_exit
from ytbot.core.startup_manager import StartupManager
from ytbot.core.user_state import UserStateManager
from ytbot.services.telegram_service import TelegramService
from ytbot.services.storage_service import StorageService
from ytbot.services.download_service import DownloadService
from ytbot.monitoring.health_monitor import HealthMonitor
from ytbot.monitoring.connection_monitor import ConnectionMonitor

logger = get_logger(__name__)


class YTBot:
    """Main YTBot application class"""

    def __init__(self):
        self.startup_manager: Optional[StartupManager] = None
        self.telegram_service: Optional[TelegramService] = None
        self.storage_service: Optional[StorageService] = None
        self.download_service: Optional[DownloadService] = None
        self.health_monitor: Optional[HealthMonitor] = None
        self.connection_monitor: Optional[ConnectionMonitor] = None
        self.user_state_manager: Optional[UserStateManager] = None

        self.running = False
        self.monitoring_tasks = []

    @log_function_entry_exit(logger)
    async def start(self):
        """Start the bot with detailed logging using StartupManager"""
        logger.info("🚀 === YTBot Starting ===")
        logger.info(f"🤖 Bot Version: {CONFIG['app']['version']}")
        logger.info(f"📊 Log Level: {CONFIG['log']['level']}")

        # Create StartupManager
        self.startup_manager = StartupManager()

        # Execute startup sequence
        startup_success = await self.startup_manager.run_startup_sequence()

        if not startup_success:
            logger.error("❌ Startup sequence failed")
            self.startup_manager.print_startup_summary()
            return False

        # Get initialized services from StartupManager
        self.telegram_service = self.startup_manager.get_service('telegram')
        self.storage_service = self.startup_manager.get_service('storage')

        # Initialize additional services not managed by StartupManager
        logger.info("� Initializing additional services...")

        # Initialize DownloadService
        self.download_service = DownloadService()
        logger.info("✅ DownloadService initialized")

        # Initialize UserStateManager
        self.user_state_manager = UserStateManager(
            timeout=CONFIG['monitor']['user_state_timeout'],
            persistence_file=None,  # Can be configured for persistence
            cleanup_interval=60
        )
        logger.info("✅ UserStateManager initialized")

        # Initialize HealthMonitor
        self.health_monitor = HealthMonitor()
        logger.info("✅ HealthMonitor initialized")

        # Initialize ConnectionMonitor
        self.connection_monitor = ConnectionMonitor()

        # Set up service connections for ConnectionMonitor
        if self.telegram_service and self.storage_service:
            self.connection_monitor.set_services(
                self.telegram_service,
                self.storage_service.nextcloud_storage
            )
        logger.info("✅ ConnectionMonitor initialized")

        # Set up handlers
        logger.info("🔧 Setting up message handlers...")
        await self._setup_handlers()
        logger.info("✅ Message handlers configured")

        # Start monitoring
        logger.info("📊 Starting system monitoring...")
        await self._start_monitoring()
        logger.info("✅ System monitoring started")

        # Start background retry task for cached files
        if self.storage_service:
            logger.info("🔄 Starting background cache retry task...")
            await self.storage_service.start_background_retry_task(
                interval_seconds=CONFIG['monitor']['cache_retry_interval']
            )
            logger.info("✅ Background cache retry task started")

        # Print startup summary
        self.startup_manager.print_startup_summary()

        logger.info("🎉 === YTBot Started Successfully ===")
        logger.info("🤖 Bot is running and ready to process requests")

        if self.telegram_service and self.telegram_service.bot:
            bot_username = self.telegram_service.bot.username
            logger.info(f"📱 Telegram: @{bot_username}")

        if self.storage_service:
            storage_info = (
                'Nextcloud + Local'
                if self.storage_service.nextcloud_available else 'Local only'
            )
            logger.info(f"💾 Storage: {storage_info}")

            cache_status = self.storage_service.get_cache_status()
            if cache_status['total_items'] > 0:
                logger.info(
                    f"📋 Cache: {cache_status['total_items']} files pending upload "
                    f"({cache_status['total_size_mb']:.2f} MB)"
                )

        self.running = True

        await self._send_startup_notification()

        return True

    @log_function_entry_exit(logger)
    async def stop(self):
        """Stop the bot with detailed logging and resource cleanup"""
        logger.info("🛑 === YTBot Stopping ===")
        logger.info("🔄 Stopping background services...")

        self.running = False

        # Stop background retry task
        if self.storage_service:
            logger.info("🔄 Stopping background cache retry task...")
            await self.storage_service.stop_background_retry_task()
            logger.info("✅ Background cache retry task stopped")

        # Stop monitoring
        if self.health_monitor:
            logger.info("📊 Stopping health monitoring...")
            self.health_monitor.stop_monitoring()
            logger.info("✅ Health monitoring stopped")

        if self.connection_monitor:
            logger.info("🔗 Stopping connection monitoring...")
            self.connection_monitor.stop_monitoring()
            logger.info("✅ Connection monitoring stopped")

        # Cancel monitoring tasks
        if self.monitoring_tasks:
            logger.info("🧹 Canceling monitoring tasks...")
            for task in self.monitoring_tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            logger.info("✅ Monitoring tasks canceled")

        # Shutdown UserStateManager
        if self.user_state_manager:
            logger.info("👤 Shutting down UserStateManager...")
            self.user_state_manager.shutdown()
            logger.info("✅ UserStateManager shutdown complete")

        # Disconnect from Telegram
        if self.telegram_service:
            logger.info("📱 Disconnecting from Telegram...")
            await self.telegram_service.disconnect()
            logger.info("✅ Disconnected from Telegram")

        # Print final status
        if self.startup_manager:
            startup_status = self.startup_manager.get_startup_status()
            logger.info(
                f"📊 Session Statistics: "
                f"Startup Duration: {startup_status['startup_time'].get('duration', 0):.2f}s"
            )

        logger.info("🛑 === YTBot Stopped ===")

    async def _setup_handlers(self):
        """Set up command and message handlers"""
        from ytbot.handlers.telegram_handler import TelegramHandler

        if not self.telegram_service:
            logger.error("❌ Cannot setup handlers: Telegram service not initialized")
            return

        # Create handler with UserStateManager
        handler = TelegramHandler(
            telegram_service=self.telegram_service,
            storage_service=self.storage_service,
            download_service=self.download_service
        )

        # Pass UserStateManager to handler if it has the attribute
        if hasattr(handler, 'state_manager') and self.user_state_manager:
            handler.state_manager = self.user_state_manager
            logger.info("✅ UserStateManager passed to TelegramHandler")

        handler.setup_handlers()
        logger.info("✅ Telegram handlers setup complete")

    async def _start_monitoring(self):
        """Start background monitoring services"""
        monitoring_tasks = []

        # Start health monitoring
        if self.health_monitor:
            health_task = asyncio.create_task(self.health_monitor.start_monitoring())
            monitoring_tasks.append(health_task)
            logger.debug("Health monitoring task created")

        # Start connection monitoring
        if self.connection_monitor:
            connection_task = asyncio.create_task(self.connection_monitor.start_monitoring())
            monitoring_tasks.append(connection_task)
            logger.debug("Connection monitoring task created")

        # Store tasks for cleanup
        self.monitoring_tasks = monitoring_tasks
        logger.info(f"📊 Started {len(monitoring_tasks)} monitoring tasks")

    async def _send_startup_notification(self):
        """Send startup notification to admin"""
        admin_chat_id = CONFIG['telegram'].get('admin_chat_id')

        if not admin_chat_id:
            logger.warning("⚠️  No admin chat ID configured, skipping startup notification")
            return

        if not self.telegram_service or not self.telegram_service.connected:
            logger.warning("⚠️  Telegram service not connected, skipping startup notification")
            return

        try:
            from datetime import datetime

            storage_info = "未知"
            if self.storage_service:
                storage_info = (
                    'Nextcloud + 本地存储'
                    if self.storage_service.nextcloud_available else '仅本地存储'
                )

            cache_info = ""
            if self.storage_service:
                cache_status = self.storage_service.get_cache_status()
                if cache_status['total_items'] > 0:
                    cache_info = (
                        f"\n📋 待上传缓存: {cache_status['total_items']} 个文件 "
                        f"({cache_status['total_size_mb']:.2f} MB)"
                    )

            startup_time = 0
            if self.startup_manager:
                startup_status = self.startup_manager.get_startup_status()
                startup_time = startup_status['startup_time'].get('duration', 0)

            notification_message = (
                f"🚀 **YTBot 启动成功**\n\n"
                f"⏰ 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"⏱️ 启动耗时: {startup_time:.2f} 秒\n"
                f"💾 存储模式: {storage_info}"
                f"{cache_info}\n\n"
                f"✅ 机器人已准备就绪，等待接收消息..."
            )

            await self.telegram_service.send_message(
                chat_id=int(admin_chat_id),
                text=notification_message
            )
            logger.info(f"✅ Startup notification sent to admin (chat_id: {admin_chat_id})")

        except Exception as e:
            logger.error(f"❌ Failed to send startup notification: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Get current bot status"""
        status = {
            "running": self.running,
            "telegram_connected": (
                self.telegram_service.connected
                if self.telegram_service else False
            ),
            "nextcloud_available": (
                self.storage_service.nextcloud_available
                if self.storage_service else False
            ),
            "local_storage_enabled": CONFIG['local_storage']['enabled'],
            "supported_platforms": (
                self.download_service.get_supported_platforms()
                if self.download_service else []
            ),
            "health_status": (
                self.health_monitor.get_health_summary()
                if self.health_monitor else {}
            ),
            "connection_status": (
                self.connection_monitor.get_connection_status()
                if self.connection_monitor else {}
            ),
            "user_state_manager": {
                "active_users": (
                    len(self.user_state_manager)
                    if self.user_state_manager else 0
                ),
                "timeout": (
                    self.user_state_manager.timeout
                    if self.user_state_manager else 0
                )
            },
            "cache_status": (
                self.storage_service.get_cache_status()
                if self.storage_service else {}
            )
        }

        # Add startup status if available
        if self.startup_manager:
            status["startup_status"] = self.startup_manager.get_startup_status()

        return status


async def main():
    """Main async function"""
    bot = YTBot()

    try:
        if not await bot.start():
            logger.error("❌ Failed to start YTBot")
            return 1

        logger.info("YTBot is running. Press Ctrl+C to stop.")

        shutdown_event = asyncio.Event()

        def signal_handler():
            logger.info("📡 Shutdown signal received")
            shutdown_event.set()

        import signal
        signal.signal(signal.SIGINT, lambda s, f: signal_handler())
        signal.signal(signal.SIGTERM, lambda s, f: signal_handler())

        if bot.telegram_service and bot.telegram_service.application:
            logger.info("🎧 Starting Telegram polling...")
            await bot.telegram_service.application.initialize()
            await bot.telegram_service.application.start()
            await bot.telegram_service.application.updater.start_polling()

            await shutdown_event.wait()

            try:
                await bot.telegram_service.application.updater.stop()
                await bot.telegram_service.application.stop()
                await bot.telegram_service.application.shutdown()
            except Exception as e:
                logger.warning(f"⚠️ Error during Telegram shutdown: {e}")
        else:
            await shutdown_event.wait()

    except KeyboardInterrupt:
        logger.info("⌨️  Keyboard interrupt received")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        logger.exception("Error details:")
        return 1
    finally:
        await bot.stop()

    return 0


def cli():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description="YTBot - Multi-platform content download and management bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ytbot                          # Run the bot with default settings
  ytbot --config /path/to/.env   # Use custom config file
  ytbot --log-level DEBUG        # Run with debug logging
  ytbot --status                 # Show bot status and exit
  ytbot --cache-status           # Show cache queue status
  ytbot --retry-cache            # Manually retry cached files upload
        """
    )

    parser.add_argument(
        "--config",
        type=str,
        help="Path to configuration file (.env)"
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set logging level (default: INFO)"
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Show bot status and exit"
    )

    parser.add_argument(
        "--cache-status",
        action="store_true",
        help="Show cache queue status and exit"
    )

    parser.add_argument(
        "--retry-cache",
        action="store_true",
        help="Manually retry uploading cached files to Nextcloud"
    )

    parser.add_argument(
        "--no-cache-retry",
        action="store_true",
        help="Disable automatic background cache retry task"
    )

    parser.add_argument(
        "--user-state-timeout",
        type=int,
        help="User state timeout in seconds (overrides config)"
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 2.0.0"
    )

    args = parser.parse_args()

    # Set up logging
    if args.log_level:
        CONFIG['log']['level'] = args.log_level

    # Override user state timeout if provided
    if args.user_state_timeout:
        CONFIG['monitor']['user_state_timeout'] = args.user_state_timeout

    # Set up exception handling
    setup_exception_handler()

    # Handle status check
    if args.status:
        print("YTBot Status Check")
        print("==================")

        # Validate config
        errors = validate_config()
        if errors:
            print(f"❌ Configuration errors: {', '.join(errors)}")
            return 1

        print("✅ Configuration: Valid")
        print("✅ Ready to start")
        return 0

    # Handle cache status check
    if args.cache_status:
        print("YTBot Cache Status")
        print("==================")

        try:
            from ytbot.services.storage_service import StorageService

            storage_service = StorageService()
            cache_status = storage_service.get_cache_status()

            print(f"Cache Enabled: {cache_status.get('cache_enabled', False)}")
            print(f"Total Items: {cache_status.get('total_items', 0)}")
            print(f"Total Size: {cache_status.get('total_size_mb', 0):.2f} MB")
            print(f"Files Exist: {cache_status.get('files_exist', 0)}")
            print(f"Files Missing: {cache_status.get('files_missing', 0)}")
            print(f"Nextcloud Available: {cache_status.get('nextcloud_available', False)}")

            content_types = cache_status.get('content_types', {})
            if content_types:
                print("\nContent Types:")
                for content_type, count in content_types.items():
                    print(f"  - {content_type}: {count}")

            return 0
        except Exception as e:
            print(f"❌ Error checking cache status: {e}")
            return 1

    # Handle manual cache retry
    if args.retry_cache:
        print("YTBot Cache Retry")
        print("=================")

        try:
            from ytbot.services.storage_service import StorageService

            async def retry_cache():
                storage_service = StorageService()
                result = await storage_service.retry_cached_files()

                print(f"Nextcloud Available: {result.get('nextcloud_available', False)}")
                print(f"Files Processed: {result.get('files_processed', 0)}")
                print(f"Files Uploaded: {result.get('files_uploaded', 0)}")
                print(f"Files Failed: {result.get('files_failed', 0)}")

                if result.get('errors'):
                    print("\nErrors:")
                    for error in result['errors']:
                        print(f"  - {error}")

                return 0 if result.get('success') else 1

            return asyncio.run(retry_cache())
        except Exception as e:
            print(f"❌ Error retrying cache: {e}")
            return 1

    # Store no-cache-retry flag in config
    if args.no_cache_retry:
        CONFIG['monitor']['cache_retry_interval'] = 0
        logger.info("🚫 Automatic cache retry disabled")

    # Run the bot
    return asyncio.run(main())


if __name__ == "__main__":
    sys.exit(cli())
