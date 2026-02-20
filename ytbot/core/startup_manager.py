"""
Startup Manager for YTBot

Manages the startup sequence with phase tracking, error handling, and rollback capabilities.
"""

import shutil
import subprocess
import sys
from datetime import datetime
from enum import Enum, auto
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field

import yt_dlp
import requests

from .config import CONFIG, validate_config
from .enhanced_logger import get_logger, log_function_entry_exit

logger = get_logger(__name__)


class StartupPhase(Enum):
    """Startup phases enumeration"""
    CONFIG_VALIDATION = auto()
    FFMPEG_CHECK = auto()
    YT_DLP_UPDATE = auto()
    TELEGRAM_CONNECTION = auto()
    NEXTCLOUD_CONNECTION = auto()
    LOCAL_STORAGE_INIT = auto()
    CACHE_CHECK = auto()
    MESSAGE_LISTENER = auto()


class PhaseStatus(Enum):
    """Phase execution status"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ROLLED_BACK = "rolled_back"


@dataclass
class PhaseResult:
    """Result of a startup phase execution"""
    phase: StartupPhase
    status: PhaseStatus
    message: str = ""
    error: Optional[str] = None
    duration: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    rollback_performed: bool = False


class StartupManager:
    """
    Manages the startup sequence for YTBot with comprehensive phase tracking,
    error handling, and rollback capabilities.
    """

    def __init__(self):
        self.phases: Dict[StartupPhase, PhaseResult] = {}
        self.phase_order: List[StartupPhase] = [
            StartupPhase.CONFIG_VALIDATION,
            StartupPhase.FFMPEG_CHECK,
            StartupPhase.YT_DLP_UPDATE,
            StartupPhase.TELEGRAM_CONNECTION,
            StartupPhase.NEXTCLOUD_CONNECTION,
            StartupPhase.LOCAL_STORAGE_INIT,
            StartupPhase.CACHE_CHECK,
            StartupPhase.MESSAGE_LISTENER,
        ]
        self.services: Dict[str, Any] = {}
        self.startup_start_time: Optional[datetime] = None
        self.startup_end_time: Optional[datetime] = None
        self._rollback_handlers: Dict[StartupPhase, Callable] = {}

        # Register rollback handlers
        self._register_rollback_handlers()

    def _register_rollback_handlers(self):
        """Register rollback handlers for each phase"""
        self._rollback_handlers = {
            StartupPhase.TELEGRAM_CONNECTION: self._rollback_telegram,
            StartupPhase.NEXTCLOUD_CONNECTION: self._rollback_nextcloud,
            StartupPhase.LOCAL_STORAGE_INIT: self._rollback_local_storage,
            StartupPhase.MESSAGE_LISTENER: self._rollback_message_listener,
        }

    @log_function_entry_exit(logger)
    async def run_startup_sequence(self) -> bool:
        """
        Execute the complete startup sequence with phase tracking and error handling.

        Returns:
            bool: True if startup completed successfully, False otherwise
        """
        self.startup_start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("🚀 YTBot Startup Sequence Initiated")
        logger.info("=" * 60)
        logger.info(f"📅 Start Time: {self.startup_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"📊 Total Phases: {len(self.phase_order)}")

        # Initialize all phases as pending
        for phase in self.phase_order:
            self.phases[phase] = PhaseResult(
                phase=phase,
                status=PhaseStatus.PENDING
            )

        try:
            # Execute each phase in order
            for phase in self.phase_order:
                success = await self._execute_phase(phase)

                if not success:
                    # Phase failed, perform rollback
                    logger.error(f"❌ Startup failed at phase: {phase.name}")
                    await self._perform_rollback()
                    return False

            # All phases completed successfully
            self.startup_end_time = datetime.now()
            duration = (self.startup_end_time - self.startup_start_time).total_seconds()

            logger.info("=" * 60)
            logger.info("✅ YTBot Startup Completed Successfully")
            logger.info("=" * 60)
            logger.info(f"📅 End Time: {self.startup_end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"⏱️  Total Duration: {duration:.2f} seconds")
            logger.info(f"📊 Phases Completed: {len(self.phase_order)}/{len(self.phase_order)}")

            return True

        except Exception as e:
            logger.critical(f"🚨 Unexpected error during startup: {e}")
            logger.exception("Startup sequence error details:")
            await self._perform_rollback()
            return False

    async def _execute_phase(self, phase: StartupPhase) -> bool:
        """
        Execute a single startup phase.

        Args:
            phase: The phase to execute

        Returns:
            bool: True if phase succeeded, False otherwise
        """
        phase_start = datetime.now()
        logger.info("")
        logger.info("-" * 60)
        logger.info(f"🔄 Phase: {phase.name}")
        logger.info(f"📝 Description: {self._get_phase_description(phase)}")
        logger.info("-" * 60)

        # Update phase status to in progress
        self.phases[phase].status = PhaseStatus.IN_PROGRESS
        self.phases[phase].timestamp = phase_start

        try:
            # Execute the phase handler
            handler = self._get_phase_handler(phase)
            success, message, error = await handler()

            # Calculate duration
            phase_end = datetime.now()
            duration = (phase_end - phase_start).total_seconds()

            # Update phase result
            self.phases[phase].status = PhaseStatus.COMPLETED if success else PhaseStatus.FAILED
            self.phases[phase].message = message
            self.phases[phase].error = error
            self.phases[phase].duration = duration

            if success:
                logger.info(f"✅ Phase completed: {phase.name}")
                logger.info(f"💬 Message: {message}")
                logger.info(f"⏱️  Duration: {duration:.2f} seconds")
            else:
                logger.error(f"❌ Phase failed: {phase.name}")
                logger.error(f"💬 Error: {error}")

            return success

        except Exception as e:
            # Handle unexpected errors
            phase_end = datetime.now()
            duration = (phase_end - phase_start).total_seconds()

            self.phases[phase].status = PhaseStatus.FAILED
            self.phases[phase].error = str(e)
            self.phases[phase].duration = duration

            logger.error(f"❌ Phase failed with exception: {phase.name}")
            logger.exception(f"Phase execution error: {e}")

            return False

    def _get_phase_handler(self, phase: StartupPhase) -> Callable:
        """Get the handler function for a phase"""
        handlers = {
            StartupPhase.CONFIG_VALIDATION: self._phase_config_validation,
            StartupPhase.FFMPEG_CHECK: self._phase_ffmpeg_check,
            StartupPhase.YT_DLP_UPDATE: self._phase_yt_dlp_update,
            StartupPhase.TELEGRAM_CONNECTION: self._phase_telegram_connection,
            StartupPhase.NEXTCLOUD_CONNECTION: self._phase_nextcloud_connection,
            StartupPhase.LOCAL_STORAGE_INIT: self._phase_local_storage_init,
            StartupPhase.CACHE_CHECK: self._phase_cache_check,
            StartupPhase.MESSAGE_LISTENER: self._phase_message_listener,
        }
        return handlers.get(phase, self._phase_unknown)

    def _get_phase_description(self, phase: StartupPhase) -> str:
        """Get human-readable description for a phase"""
        descriptions = {
            StartupPhase.CONFIG_VALIDATION: "Loading and validating configuration settings",
            StartupPhase.FFMPEG_CHECK: "Checking FFmpeg availability for media processing",
            StartupPhase.YT_DLP_UPDATE: "Checking and updating yt-dlp to latest version",
            StartupPhase.TELEGRAM_CONNECTION: "Establishing connection to Telegram Bot API",
            StartupPhase.NEXTCLOUD_CONNECTION: "Connecting to Nextcloud storage server",
            StartupPhase.LOCAL_STORAGE_INIT: "Initializing local storage directories",
            StartupPhase.CACHE_CHECK: "Checking for pending cached files to upload",
            StartupPhase.MESSAGE_LISTENER: "Starting message listener loop",
        }
        return descriptions.get(phase, "Unknown phase")

    # Phase Handlers

    async def _phase_config_validation(self) -> tuple[bool, str, Optional[str]]:
        """Phase 1: Load and validate configuration"""
        logger.info("🔍 Validating configuration...")

        try:
            # Validate configuration
            errors = validate_config()

            if errors:
                # Check if errors are critical (required fields) or warnings (optional fields)
                critical_errors = [e for e in errors if "required" in e.lower()]
                warning_errors = [e for e in errors if "recommended" in e.lower()]

                if critical_errors:
                    error_msg = f"Critical configuration errors: {'; '.join(critical_errors)}"
                    return False, "Configuration validation failed", error_msg

                if warning_errors:
                    warning_str = '; '.join(warning_errors)
                    logger.warning(f"⚠️  Configuration warnings: {warning_str}")
                    warning_count = len(warning_errors)
                    msg = f"Configuration validated with {warning_count} warnings"
                    return True, msg, None

            # Log configuration summary
            logger.info("✅ Configuration loaded successfully")
            logger.info(f"📊 Bot Version: {CONFIG['app']['version']}")
            logger.info(f"📝 Log Level: {CONFIG['log']['level']}")
            logger.info(f"💾 Local Storage: {CONFIG['local_storage']['path']}")
            nc_url = CONFIG['nextcloud']['url'] or 'Not configured'
            logger.info(f"☁️  Nextcloud URL: {nc_url}")

            return True, "Configuration validated successfully", None

        except Exception as e:
            return False, "Configuration validation failed", str(e)

    async def _phase_ffmpeg_check(self) -> tuple[bool, str, Optional[str]]:
        """Phase 2: Check FFmpeg availability"""
        logger.info("🎬 Checking FFmpeg availability...")

        try:
            # Check if ffmpeg is available
            ffmpeg_path = shutil.which('ffmpeg')

            if ffmpeg_path:
                logger.info(f"✅ FFmpeg found at: {ffmpeg_path}")

                # Get FFmpeg version
                try:
                    result = subprocess.run(
                        ['ffmpeg', '-version'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        version_line = result.stdout.split('\n')[0]
                        logger.info(f"📹 FFmpeg version: {version_line}")
                        return True, f"FFmpeg available: {version_line}", None
                except Exception as e:
                    logger.warning(f"⚠️  Could not get FFmpeg version: {e}")
                    return True, "FFmpeg is available", None

            else:
                # FFmpeg not found
                error_msg = (
                    "FFmpeg is not installed or not in PATH.\n"
                    "FFmpeg is required for media processing (audio/video conversion).\n\n"
                    "Please install FFmpeg:\n"
                    "  - macOS: brew install ffmpeg\n"
                    "  - Ubuntu/Debian: sudo apt-get install ffmpeg\n"
                    "  - Windows: Download from https://ffmpeg.org/download.html\n"
                    "  - Or use: pip install ffmpeg-python"
                )
                logger.error("❌ FFmpeg not found")
                logger.error(error_msg)

                return False, "FFmpeg not available", error_msg

        except Exception as e:
            return False, "FFmpeg check failed", str(e)

    async def _phase_yt_dlp_update(self) -> tuple[bool, str, Optional[str]]:
        """Phase 3: Check and update yt-dlp"""
        logger.info("📦 Checking yt-dlp version...")

        try:
            # Get current version
            current_version = yt_dlp.version.__version__
            logger.info(f"📌 Current yt-dlp version: {current_version}")

            # Check if version check is enabled
            if not CONFIG['download']['check_yt_dlp_version']:
                logger.info("⏭️  yt-dlp version check disabled in configuration")
                return True, f"yt-dlp version check skipped (current: {current_version})", None

            # Get latest version from PyPI
            logger.info("🔍 Checking for updates on PyPI...")
            try:
                response = requests.get(
                    'https://pypi.org/pypi/yt-dlp/json',
                    timeout=CONFIG['download']['version_check_timeout']
                )
                response.raise_for_status()
                latest_version = response.json()['info']['version']
                logger.info(f"🌟 Latest yt-dlp version: {latest_version}")

                # Normalize versions for comparison
                current_normalized = self._normalize_version(current_version)
                latest_normalized = self._normalize_version(latest_version)

                # Compare versions
                if current_normalized < latest_normalized:
                    logger.warning(f"⚠️  yt-dlp is outdated: {current_version} < {latest_version}")
                    logger.info("🔄 Attempting to update yt-dlp...")

                    # Try to update
                    update_success = await self._update_yt_dlp()

                    if update_success:
                        return True, f"yt-dlp updated to {latest_version}", None
                    else:
                        warning_msg = (
                            f"yt-dlp update failed. Current: {current_version}, "
                            f"Latest: {latest_version}. "
                            "Update manually: pip install --upgrade yt-dlp"
                        )
                        logger.warning(warning_msg)
                        # Not a critical error, continue with current version
                        return True, warning_msg, None
                else:
                    logger.info("✅ yt-dlp is up to date")
                return True, "yt-dlp is up to date", None

            except requests.RequestException as e:
                logger.warning(f"⚠️  Could not check for updates: {e}")
                return True, "yt-dlp version check failed", None

        except Exception as e:
            logger.error(f"❌ yt-dlp version check failed: {e}")
            # Not a critical error, continue
            return True, f"yt-dlp check failed but continuing: {str(e)}", None

    def _normalize_version(self, version: str) -> str:
        """Normalize version string for comparison"""
        parts = version.split('.')
        normalized_parts = [str(int(part)) if part.isdigit() else part for part in parts]
        return '.'.join(normalized_parts)

    async def _update_yt_dlp(self) -> bool:
        """Update yt-dlp to the latest version"""
        try:
            logger.info("🔄 Updating yt-dlp via pip...")

            # Run pip update command
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp'],
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode == 0:
                logger.info("✅ yt-dlp updated successfully")
                logger.debug(f"Update output: {result.stdout}")
                return True
            else:
                logger.error(f"❌ yt-dlp update failed: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error("❌ yt-dlp update timed out")
            return False
        except Exception as e:
            logger.error(f"❌ yt-dlp update error: {e}")
            return False

    async def _phase_telegram_connection(self) -> tuple[bool, str, Optional[str]]:
        """Phase 4: Connect to Telegram Bot API"""
        logger.info("📱 Connecting to Telegram Bot API...")

        try:
            from ..services.telegram_service import TelegramService

            # Create Telegram service
            telegram_service = TelegramService()
            self.services['telegram'] = telegram_service

            # Connect to Telegram
            success = await telegram_service.connect()

            if success:
                bot_info = await telegram_service.get_bot_info()
                if bot_info:
                    username = bot_info.get('username', 'Unknown')
                    logger.info(f"✅ Connected to Telegram as @{username}")
                    return True, f"Connected to Telegram as @{username}", None
                else:
                    return True, "Connected to Telegram successfully", None
            else:
                return False, "Failed to connect to Telegram", "Connection attempt returned False"

        except Exception as e:
            logger.error(f"❌ Telegram connection failed: {e}")
            return False, "Telegram connection failed", str(e)

    async def _phase_nextcloud_connection(self) -> tuple[bool, str, Optional[str]]:
        """Phase 5: Connect to Nextcloud server"""
        logger.info("☁️  Connecting to Nextcloud server...")

        try:
            # Check if Nextcloud is configured
            if not CONFIG['nextcloud']['url']:
                logger.info("⏭️  Nextcloud not configured, skipping")
                return True, "Nextcloud not configured (optional)", None

            from ..services.storage_service import StorageService

            # Create storage service
            storage_service = StorageService()
            self.services['storage'] = storage_service

            # Check Nextcloud availability
            if storage_service.nextcloud_available:
                logger.info(f"✅ Nextcloud connected: {CONFIG['nextcloud']['url']}")
                return True, f"Connected to Nextcloud at {CONFIG['nextcloud']['url']}", None
            else:
                logger.warning("⚠️  Nextcloud connection failed, will use local storage")
                # Not a critical error, can fall back to local storage
                return True, "Nextcloud unavailable, using local storage fallback", None

        except Exception as e:
            logger.warning(f"⚠️  Nextcloud connection error: {e}")
            # Not a critical error, can continue with local storage
            return True, f"Nextcloud connection failed but continuing: {str(e)}", None

    async def _phase_local_storage_init(self) -> tuple[bool, str, Optional[str]]:
        """Phase 6: Initialize local storage"""
        logger.info("💾 Initializing local storage...")

        try:
            from ..storage.local_storage import LocalStorageManager

            # Create local storage manager
            local_storage = LocalStorageManager()
            self.services['local_storage'] = local_storage

            if not local_storage.enabled:
                logger.warning("⚠️  Local storage is disabled")
                return True, "Local storage is disabled", None

            # Check storage directory
            storage_path = local_storage.storage_path
            logger.info(f"📁 Storage path: {storage_path}")

            # Get storage info
            available_space = local_storage.get_available_space_mb()
            current_usage = local_storage.get_storage_usage_mb()
            max_size = local_storage.max_size_mb

            logger.info("📊 Storage Statistics:")
            logger.info(f"  - Available: {available_space:.1f} MB")
            logger.info(f"  - Used: {current_usage:.1f} MB")
            logger.info(f"  - Max Limit: {max_size:.1f} MB")

            # Check if storage is accessible
            if not storage_path.exists():
                logger.error("❌ Storage directory does not exist")
                error_msg = f"Path not found: {storage_path}"
                return False, "Storage directory initialization failed", error_msg

            logger.info("✅ Local storage initialized successfully")
            return True, f"Local storage ready at {storage_path}", None

        except Exception as e:
            logger.error(f"❌ Local storage initialization failed: {e}")
            return False, "Local storage initialization failed", str(e)

    async def _phase_cache_check(self) -> tuple[bool, str, Optional[str]]:
        """Phase 7: Check for pending cached files"""
        logger.info("🔍 Checking for pending cached files...")

        try:
            # Check if local storage is available
            if 'local_storage' not in self.services:
                logger.info("⏭️  Local storage not available, skipping cache check")
                return True, "Cache check skipped (no local storage)", None

            local_storage = self.services['local_storage']
            storage_path = local_storage.storage_path

            # Find cached files
            cached_files = []
            if storage_path.exists():
                for file_path in storage_path.rglob('*'):
                    if file_path.is_file():
                        cached_files.append(file_path)

            if cached_files:
                count = len(cached_files)
                logger.info(f"📦 Found {count} cached file(s)")
                msg = "These files will be available for upload when Nextcloud is connected"
                logger.info(f"💡 {msg}")

                # Log file details
                for i, file_path in enumerate(cached_files[:5]):
                    file_size = file_path.stat().st_size / (1024 * 1024)
                    logger.info(f"  {i + 1}. {file_path.name} ({file_size:.2f} MB)")

                if len(cached_files) > 5:
                    remaining = len(cached_files) - 5
                    logger.info(f"  ... and {remaining} more files")

                return True, f"Found {count} cached file(s)", None
            else:
                logger.info("✅ No cached files found")
                return True, "No cached files found", None

        except Exception as e:
            logger.warning(f"⚠️  Cache check failed: {e}")
            # Not a critical error
            return True, f"Cache check failed but continuing: {str(e)}", None

    async def _phase_message_listener(self) -> tuple[bool, str, Optional[str]]:
        """Phase 8: Start message listener"""
        logger.info("🎧 Preparing message listener...")

        try:
            # This phase prepares the message listener but doesn't start it yet
            # The actual polling will be started by the main application

            if 'telegram' not in self.services:
                error_msg = "Cannot start listener without Telegram connection"
                return False, "Telegram service not available", error_msg

            telegram_service = self.services['telegram']

            if not telegram_service.connected:
                error_msg = "Cannot start listener without active connection"
                return False, "Telegram not connected", error_msg

            logger.info("✅ Message listener ready")
            logger.info("💡 Listener will start polling after startup completes")

            return True, "Message listener prepared successfully", None

        except Exception as e:
            logger.error(f"❌ Message listener preparation failed: {e}")
            return False, "Message listener preparation failed", str(e)

    async def _phase_unknown(self) -> tuple[bool, str, Optional[str]]:
        """Handler for unknown phases"""
        return False, "Unknown phase", "No handler registered for this phase"

    # Rollback Handlers

    async def _perform_rollback(self):
        """Perform rollback for all completed phases"""
        logger.info("")
        logger.info("=" * 60)
        logger.info("🔄 Initiating Rollback Sequence")
        logger.info("=" * 60)

        # Get completed phases in reverse order
        completed_phases = [
            phase for phase in reversed(self.phase_order)
            if self.phases[phase].status == PhaseStatus.COMPLETED
        ]

        if not completed_phases:
            logger.info("ℹ️  No completed phases to roll back")
            return

        logger.info(f"📊 Rolling back {len(completed_phases)} completed phase(s)")

        for phase in completed_phases:
            await self._rollback_phase(phase)

        logger.info("=" * 60)
        logger.info("✅ Rollback Sequence Completed")
        logger.info("=" * 60)

    async def _rollback_phase(self, phase: StartupPhase):
        """Rollback a specific phase"""
        logger.info(f"🔄 Rolling back phase: {phase.name}")

        try:
            # Get rollback handler
            handler = self._rollback_handlers.get(phase)

            if handler:
                await handler()
                self.phases[phase].status = PhaseStatus.ROLLED_BACK
                self.phases[phase].rollback_performed = True
                logger.info(f"✅ Rollback completed for: {phase.name}")
            else:
                logger.info(f"ℹ️  No rollback handler for: {phase.name}")

        except Exception as e:
            logger.error(f"❌ Rollback failed for {phase.name}: {e}")
            logger.exception("Rollback error details:")

    async def _rollback_telegram(self):
        """Rollback Telegram connection"""
        if 'telegram' in self.services:
            logger.info("📴 Disconnecting from Telegram...")
            await self.services['telegram'].disconnect()
            del self.services['telegram']

    async def _rollback_nextcloud(self):
        """Rollback Nextcloud connection"""
        if 'storage' in self.services:
            logger.info("☁️  Disconnecting from Nextcloud...")
            # Nextcloud doesn't require explicit disconnection
            del self.services['storage']

    async def _rollback_local_storage(self):
        """Rollback local storage initialization"""
        if 'local_storage' in self.services:
            logger.info("💾 Cleaning up local storage references...")
            del self.services['local_storage']

    async def _rollback_message_listener(self):
        """Rollback message listener"""
        if 'telegram' in self.services:
            logger.info("🛑 Stopping message listener...")
            await self.services['telegram'].stop_polling()

    # Status and Reporting Methods

    def get_startup_status(self) -> Dict[str, Any]:
        """Get comprehensive startup status"""
        status = {
            "startup_time": {
                "start": self.startup_start_time.isoformat() if self.startup_start_time else None,
                "end": self.startup_end_time.isoformat() if self.startup_end_time else None,
                "duration": (
                    (self.startup_end_time - self.startup_start_time).total_seconds()
                    if self.startup_start_time and self.startup_end_time else None
                )
            },
            "phases": {},
            "services_initialized": list(self.services.keys()),
            "overall_status": self._get_overall_status()
        }

        # Add phase details
        for phase in self.phase_order:
            result = self.phases[phase]
            status["phases"][phase.name] = {
                "status": result.status.value,
                "message": result.message,
                "error": result.error,
                "duration": result.duration,
                "timestamp": result.timestamp.isoformat(),
                "rollback_performed": result.rollback_performed
            }

        return status

    def _get_overall_status(self) -> str:
        """Get overall startup status"""
        if not self.phases:
            return "not_started"

        # Check if all phases completed
        all_completed = all(
            self.phases[phase].status == PhaseStatus.COMPLETED
            for phase in self.phase_order
        )

        if all_completed:
            return "completed"

        # Check if any phase failed
        any_failed = any(
            self.phases[phase].status == PhaseStatus.FAILED
            for phase in self.phase_order
        )

        if any_failed:
            return "failed"

        # Check if in progress
        any_in_progress = any(
            self.phases[phase].status == PhaseStatus.IN_PROGRESS
            for phase in self.phase_order
        )

        if any_in_progress:
            return "in_progress"

        return "pending"

    def print_startup_summary(self):
        """Print a formatted startup summary"""
        logger.info("")
        logger.info("=" * 60)
        logger.info("📊 STARTUP SUMMARY")
        logger.info("=" * 60)

        for phase in self.phase_order:
            result = self.phases[phase]
            status_emoji = {
                PhaseStatus.COMPLETED: "✅",
                PhaseStatus.FAILED: "❌",
                PhaseStatus.SKIPPED: "⏭️",
                PhaseStatus.ROLLED_BACK: "🔄",
                PhaseStatus.IN_PROGRESS: "🔄",
                PhaseStatus.PENDING: "⏳"
            }.get(result.status, "❓")

            logger.info(f"{status_emoji} {phase.name}: {result.status.value}")
            if result.message:
                logger.info(f"   💬 {result.message}")
            if result.error:
                logger.info(f"   ⚠️  Error: {result.error}")
            if result.duration > 0:
                logger.info(f"   ⏱️  Duration: {result.duration:.2f}s")

        logger.info("=" * 60)
        logger.info(f"Overall Status: {self._get_overall_status().upper()}")
        logger.info("=" * 60)

    def get_service(self, service_name: str) -> Optional[Any]:
        """Get a service instance by name"""
        return self.services.get(service_name)
