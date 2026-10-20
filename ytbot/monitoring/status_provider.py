"""System status provider for /status command.

Replaces the broken reverse dependency on cli.YTBot (which created a
fresh instance every time, always showing "stopped").  Instead it reads
from the process-wide service factories so the report reflects real state.
"""

from typing import Dict, Any


def build_status_report() -> Dict[str, Any]:
    """Build a system status report dict without importing YTBot."""
    from ..services.storage_service import get_storage_service
    from ..services.download_service import get_download_service
    from ..services.telegram_service import TelegramService
    from ..core.config import get_config

    storage = get_storage_service()
    download = get_download_service()
    config = get_config()

    return {
        "nextcloud_available": storage.nextcloud_available,
        "local_storage_enabled": config.local_storage.enabled,
        "supported_platforms": download.get_supported_platforms(),
        "telegram_connected": TelegramService().connected,
    }
