"""
Health monitoring service for YTBot

Monitors system health, resource usage, and service availability.
"""

import asyncio
import time
import psutil
from typing import Dict, Any, Optional

from ..core.config import CONFIG
from ..core.logger import get_logger

logger = get_logger(__name__)


class HealthMonitor:
    """System health and resource monitoring"""

    def __init__(self):
        self.monitoring = False
        self.last_check = 0
        self.check_interval = CONFIG['monitor']['interval']
        self.min_disk_space = CONFIG['monitor']['min_disk_space']
        self.max_cpu_load = CONFIG['monitor']['max_cpu_load']
        self.memory_threshold = CONFIG['monitor']['memory_threshold']

    async def start_monitoring(self):
        """Start health monitoring"""
        self.monitoring = True
        logger.info("Health monitoring started")

        while self.monitoring:
            try:
                await self._perform_health_check()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                logger.info("Health monitoring stopped")
                break
            except Exception as e:
                logger.error(f"Health monitoring error: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retrying

    def stop_monitoring(self):
        """Stop health monitoring"""
        self.monitoring = False

    async def _perform_health_check(self):
        """Perform a comprehensive health check"""
        try:
            health_status = self._get_system_health()

            # Check for critical issues
            if health_status['disk_space_mb'] < self.min_disk_space:
                await self._handle_low_disk_space(health_status)

            if health_status['cpu_percent'] > (self.max_cpu_load * 100):
                await self._handle_high_cpu_usage(health_status)

            if health_status['memory_available_mb'] < self.memory_threshold:
                await self._handle_low_memory(health_status)

            self.last_check = time.time()

        except Exception as e:
            logger.error(f"Health check failed: {e}")

    def _get_system_health(self) -> Dict[str, Any]:
        """Get current system health metrics"""
        try:
            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=1)

            # Memory usage
            memory = psutil.virtual_memory()
            memory_available_mb = memory.available / (1024 * 1024)
            memory_percent = memory.percent

            # Disk usage
            disk = psutil.disk_usage('/')
            disk_space_mb = disk.free / (1024 * 1024)
            disk_percent = (disk.used / disk.total) * 100

            # Load average (Unix-like systems)
            try:
                load_avg = psutil.getloadavg()
            except AttributeError:
                load_avg = None

            return {
                "timestamp": time.time(),
                "cpu_percent": cpu_percent,
                "memory_available_mb": memory_available_mb,
                "memory_percent": memory_percent,
                "disk_space_mb": disk_space_mb,
                "disk_percent": disk_percent,
                "load_average": load_avg,
                "status": "healthy" if self._is_healthy(cpu_percent, memory_available_mb, disk_space_mb) else "warning"
            }

        except Exception as e:
            logger.error(f"Failed to get system health: {e}")
            return {"status": "error", "error": str(e)}

    def _is_healthy(self, cpu_percent: float, memory_available_mb: float, disk_space_mb: float) -> bool:
        """Check if system is healthy based on metrics"""
        return (
            cpu_percent < (self.max_cpu_load * 100) and
            memory_available_mb > self.memory_threshold and
            disk_space_mb > self.min_disk_space
        )

    async def _handle_low_disk_space(self, health_status: Dict[str, Any]):
        """Handle low disk space situation"""
        logger.warning(f"Low disk space: {health_status['disk_space_mb']:.1f}MB available")

        # Trigger cleanup if local storage is enabled
        if CONFIG['local_storage']['enabled']:
            try:
                from ..storage.local_storage import cleanup_local_storage
                cleanup_result = await cleanup_local_storage()
                logger.info(f"Automatic cleanup completed: {cleanup_result}")
            except Exception as e:
                logger.error(f"Automatic cleanup failed: {e}")

    async def _handle_high_cpu_usage(self, health_status: Dict[str, Any]):
        """Handle high CPU usage"""
        logger.warning(f"High CPU usage: {health_status['cpu_percent']:.1f}%")

        # Could implement throttling or other mitigation strategies here
        # For now, just log the issue

    async def _handle_low_memory(self, health_status: Dict[str, Any]):
        """Handle low memory situation"""
        logger.warning(f"Low memory: {health_status['memory_available_mb']:.1f}MB available")

        # Could implement memory cleanup or other mitigation strategies here
        # For now, just log the issue

    def get_health_summary(self) -> Dict[str, Any]:
        """Get a summary of current health status"""
        try:
            current_health = self._get_system_health()

            return {
                "monitoring_active": self.monitoring,
                "last_check": self.last_check,
                "current_status": current_health,
                "thresholds": {
                    "min_disk_space_mb": self.min_disk_space,
                    "max_cpu_percent": self.max_cpu_load * 100,
                    "min_memory_mb": self.memory_threshold
                }
            }
        except Exception as e:
            logger.error(f"Failed to get health summary: {e}")
            return {"error": str(e)}