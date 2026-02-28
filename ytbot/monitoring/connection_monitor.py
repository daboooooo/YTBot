"""
Connection monitoring service for YTBot

Monitors connectivity to external services (Telegram, Nextcloud, etc.)
"""

import asyncio
import time
import socket
from typing import Dict, Any, Optional, Callable

from ..core.logger import get_logger
from ..services.telegram_service import TelegramService
from ..storage.nextcloud_storage import NextcloudStorage

logger = get_logger(__name__)


class ConnectionMonitor:
    """Monitor connectivity to external services"""

    def __init__(self):
        self.monitoring = False
        self.last_checks: Dict[str, float] = {}
        self.check_intervals = {
            "telegram": 60,   # 1 minute - check more frequently for faster reconnection
            "nextcloud": 300,  # 5 minutes
            "network": 30,    # 30 seconds
        }
        self.status: Dict[str, bool] = {
            "telegram": False,
            "nextcloud": False,
            "network": True,  # Assume network is available initially
        }
        self.telegram_service: Optional[TelegramService] = None
        self.nextcloud_storage: Optional[NextcloudStorage] = None
        self._reconnect_in_progress = False
        self._on_telegram_reconnect: Optional[Callable] = None

    def set_services(
        self,
        telegram_service: TelegramService,
        nextcloud_storage: NextcloudStorage,
        on_reconnect_callback: Optional[Callable] = None
    ):
        """Set the services to monitor"""
        self.telegram_service = telegram_service
        self.nextcloud_storage = nextcloud_storage
        self._on_telegram_reconnect = on_reconnect_callback

    async def start_monitoring(self):
        """Start connection monitoring"""
        self.monitoring = True
        logger.info("Connection monitoring started")

        while self.monitoring:
            try:
                await self._perform_connection_checks()
                await asyncio.sleep(60)  # Check every minute
            except asyncio.CancelledError:
                logger.info("Connection monitoring stopped")
                break
            except Exception as e:
                logger.error(f"Connection monitoring error: {e}")
                await asyncio.sleep(60)

    def stop_monitoring(self):
        """Stop connection monitoring"""
        self.monitoring = False

    async def _perform_connection_checks(self):
        """Perform all connection checks"""
        current_time = time.time()

        # Check network connectivity
        if current_time - self.last_checks.get("network", 0) > self.check_intervals["network"]:
            await self._check_network_connection()
            self.last_checks["network"] = current_time

        # Check Telegram connection
        telegram_check_needed = (
            self.telegram_service and
            current_time - self.last_checks.get("telegram", 0) > self.check_intervals["telegram"]
        )
        if telegram_check_needed:
            await self._check_telegram_connection()
            self.last_checks["telegram"] = current_time

        # Check Nextcloud connection
        nextcloud_check_needed = (
            self.nextcloud_storage and
            current_time - self.last_checks.get("nextcloud", 0) > self.check_intervals["nextcloud"]
        )
        if nextcloud_check_needed:
            await self._check_nextcloud_connection()
            self.last_checks["nextcloud"] = current_time

    async def _check_network_connection(self):
        """Check general network connectivity"""
        try:
            # Test connection to multiple DNS servers
            test_hosts = ['8.8.8.8', '1.1.1.1', '208.67.222.222']

            for host in test_hosts:
                try:
                    # Create socket connection in executor
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None,
                        lambda: self._create_socket_connection(host, 53, 5)
                    )
                    self.status["network"] = True
                    logger.debug(f"Network connection test successful: {host}")
                    return
                except Exception:
                    continue

            # All hosts failed
            self.status["network"] = False
            logger.warning("Network connection test failed for all hosts")

        except Exception as e:
            logger.error(f"Network connection check error: {e}")
            self.status["network"] = False

    async def _check_telegram_connection(self):
        """Check Telegram connection and auto-reconnect if needed"""
        if not self.telegram_service:
            self.status["telegram"] = False
            return

        was_connected = self.status["telegram"]

        try:
            # Check if Telegram service is connected and healthy
            is_healthy = await self.telegram_service.check_connection_health()

            if is_healthy:
                if not was_connected:
                    logger.info("✅ Telegram connection restored")
                self.status["telegram"] = True
                logger.debug("Telegram connection test successful")
            else:
                self.status["telegram"] = False
                logger.warning("Telegram connection test failed")

                # Attempt reconnection if network is available
                if self.status["network"] and not self._reconnect_in_progress:
                    await self._attempt_telegram_reconnect()

        except Exception as e:
            logger.error(f"Telegram connection check error: {e}")
            self.status["telegram"] = False

            # Attempt reconnection if network is available
            if self.status["network"] and not self._reconnect_in_progress:
                await self._attempt_telegram_reconnect()

    async def _attempt_telegram_reconnect(self):
        """Attempt to reconnect to Telegram servers"""
        if self._reconnect_in_progress:
            return

        self._reconnect_in_progress = True
        logger.info("🔄 Telegram connection lost, attempting reconnection...")

        try:
            if self.telegram_service:
                success = await self.telegram_service.reconnect()

                if success:
                    logger.info("✅ Telegram reconnection successful")
                    self.status["telegram"] = True

                    # Notify callback if set
                    if self._on_telegram_reconnect:
                        try:
                            await self._on_telegram_reconnect()
                        except Exception as e:
                            logger.error(f"Error in reconnect callback: {e}")
                else:
                    logger.error("❌ Telegram reconnection failed")
                    self.status["telegram"] = False

        except Exception as e:
            logger.error(f"Error during Telegram reconnection: {e}")
            self.status["telegram"] = False

        finally:
            self._reconnect_in_progress = False

    async def _check_nextcloud_connection(self):
        """Check Nextcloud connection"""
        if not self.nextcloud_storage:
            self.status["nextcloud"] = False
            return

        try:
            # Check Nextcloud connection
            if self.nextcloud_storage.check_connection():
                self.status["nextcloud"] = True
                logger.debug("Nextcloud connection test successful")
            else:
                self.status["nextcloud"] = False
                logger.warning("Nextcloud connection test failed")

        except Exception as e:
            logger.error(f"Nextcloud connection check error: {e}")
            self.status["nextcloud"] = False

    def _create_socket_connection(self, host: str, port: int, timeout: int):
        """Create a socket connection to test connectivity"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            sock.close()
            return True
        except Exception:
            sock.close()
            return False

    def get_connection_status(self) -> Dict[str, Any]:
        """Get current connection status"""
        return {
            "status": self.status.copy(),
            "last_checks": self.last_checks.copy(),
            "monitoring_active": self.monitoring,
            "check_intervals": self.check_intervals.copy()
        }

    def is_service_available(self, service: str) -> bool:
        """Check if a specific service is available"""
        return self.status.get(service, False)

    async def force_check(self, service: str) -> bool:
        """Force an immediate check for a specific service"""
        if service == "network":
            await self._check_network_connection()
        elif service == "telegram" and self.telegram_service:
            await self._check_telegram_connection()
        elif service == "nextcloud" and self.nextcloud_storage:
            await self._check_nextcloud_connection()
        else:
            logger.warning(f"Unknown service or service not configured: {service}")
            return False

        return self.status.get(service, False)