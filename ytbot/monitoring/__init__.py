"""
Monitoring and health check services for YTBot.

This module provides comprehensive monitoring capabilities for tracking system
health, resource usage, and connectivity to external services. It enables
proactive detection of issues before they impact the application's operation.

The monitoring system runs as background tasks that periodically check various
health metrics and connectivity status, logging warnings when thresholds are
exceeded or services become unavailable.

Exported Components:
    HealthMonitor: class
        System health and resource monitoring service. Tracks CPU usage,
        memory availability, and disk space. Automatically triggers cleanup
        actions when resources are low. Runs as a background async task.

    ConnectionMonitor: class
        Connectivity monitoring service for external services. Monitors
        network connectivity, Telegram API availability, and Nextcloud
        connection status. Provides real-time status updates and forced
        check capabilities.

Monitored Metrics:
    System Health:
        - CPU usage percentage
        - Available memory (MB)
        - Available disk space (MB)
        - System load average (Unix-like systems)

    Service Connectivity:
        - Network connectivity (DNS servers)
        - Telegram API availability
        - Nextcloud WebDAV availability

Example:
    >>> from ytbot.monitoring import HealthMonitor, ConnectionMonitor
    >>>
    >>> # Initialize monitors
    >>> health_monitor = HealthMonitor()
    >>> connection_monitor = ConnectionMonitor()
    >>>
    >>> # Start health monitoring
    >>> import asyncio
    >>> asyncio.create_task(health_monitor.start_monitoring())
    >>>
    >>> # Configure connection monitoring
    >>> connection_monitor.set_services(telegram_service, nextcloud_storage)
    >>> asyncio.create_task(connection_monitor.start_monitoring())
    >>>
    >>> # Get current health status
    >>> health_summary = health_monitor.get_health_summary()
    >>> print(f"CPU: {health_summary['current_status']['cpu_percent']}%")
    >>>
    >>> # Check service availability
    >>> if connection_monitor.is_service_available("telegram"):
    ...     print("Telegram is available")
    >>>
    >>> # Get all connection statuses
    >>> status = connection_monitor.get_connection_status()
    >>> print(f"Network: {status['status']['network']}")
    >>> print(f"Telegram: {status['status']['telegram']}")
    >>> print(f"Nextcloud: {status['status']['nextcloud']}")
"""

from .health_monitor import HealthMonitor
from .connection_monitor import ConnectionMonitor

__all__ = ["HealthMonitor", "ConnectionMonitor"]
