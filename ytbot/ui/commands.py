"""
Command processor for terminal UI.

Handles slash commands (/help, /status, etc.) and provides
extensible command registration system.
"""

from typing import Callable, Dict, List, Any
from dataclasses import dataclass
import logging

from .formatter import OutputFormatter

logger = logging.getLogger(__name__)


@dataclass
class CommandContext:
    """Context passed to command handlers."""
    args: str = ""
    terminal_ui: Any = None  # Reference to TerminalUI instance
    event_bus: Any = None    # Reference to EventBus


@dataclass
class Command:
    """Represents a registered command."""
    name: str
    description: str
    handler: Callable
    aliases: List[str] = None
    requires_args: bool = False


class CommandRegistry:
    """
    Registry and dispatcher for terminal commands.

    Provides extensible command system with:
    - Slash command parsing
    - Argument handling
    - Help generation
    - Alias support
    """

    def __init__(self):
        self._commands: Dict[str, Command] = {}
        self._register_builtin_commands()

    def _register_builtin_commands(self):
        """Register all built-in commands."""
        # Register 8 built-in commands:
        # 1. /help - Show help (aliases: /?, /h)
        # 2. /status - System status
        # 3. /tasks - Task list
        # 4. /cancel <id> - Cancel task (requires args)
        # 5. /storage - Storage status
        # 6. /log <level> - Log level (requires args)
        # 7. /clear - Clear screen
        # 8. /exit - Exit (aliases: /quit, q)

        self.register(
            name="/help",
            description="Show available commands and usage",
            handler=self._cmd_help,
            aliases=["/?", "/h"]
        )

        self.register(
            name="/status",
            description="Display system status (CPU, memory, disk)",
            handler=self._cmd_status
        )

        self.register(
            name="/tasks",
            description="Show download task list",
            handler=self._cmd_tasks
        )

        self.register(
            name="/cancel",
            description="Cancel a download task by ID",
            handler=self._cmd_cancel,
            requires_args=True
        )

        self.register(
            name="/storage",
            description="Show storage status (local & Nextcloud)",
            handler=self._cmd_storage
        )

        self.register(
            name="/log",
            description="Set log level (debug/info/warning/error)",
            handler=self._cmd_log,
            requires_args=True
        )

        self.register(
            name="/clear",
            description="Clear the screen",
            handler=self._cmd_clear
        )

        self.register(
            name="/exit",
            description="Exit YTBot gracefully",
            handler=self._cmd_exit,
            aliases=["/quit", "q"]
        )

    def register(self, name: str, description: str, handler: Callable,
                 aliases: List[str] = None, requires_args: bool = False):
        """
        Register a new command.

        Args:
            name: Command name (e.g., "/help")
            description: Human-readable description of the command
            handler: Callable that handles the command execution
            aliases: Optional list of alternative names for this command
            requires_args: Whether the command requires arguments
        """
        cmd = Command(
            name=name,
            description=description,
            handler=handler,
            aliases=aliases or [],
            requires_args=requires_args
        )

        self._commands[name.lower()] = cmd

        for alias in (aliases or []):
            self._commands[alias.lower()] = cmd

        logger.debug(f"Registered command: {name}")

    def parse_command(self, input_str: str) -> tuple:
        """
        Parse user input into command and arguments.

        Args:
            input_str: Raw user input string

        Returns:
            Tuple of (command_name, args_string) or (None, original_input)
            if not a command
        """
        input_str = input_str.strip()

        if not input_str.startswith("/"):
            return None, input_str

        parts = input_str.split(maxsplit=1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        return cmd_name, args

    async def execute(self, input_str: str, context: CommandContext) -> bool:
        """
        Execute a command from user input.

        Args:
            input_str: Raw user input string
            context: CommandContext containing execution environment

        Returns:
            True if was a command (and handled), False if not a command
        """
        cmd_name, args = self.parse_command(input_str)

        if cmd_name is None:
            return False

        cmd = self._commands.get(cmd_name)

        if cmd is None:
            if context.terminal_ui:
                context.terminal_ui.print_error(f"Unknown command: {cmd_name}")
                context.terminal_ui.print_info("Type /help for available commands")
            return True

        if cmd.requires_args and not args:
            if context.terminal_ui:
                context.terminal_ui.print_warning(
                    f"Command {cmd_name} requires arguments. Usage: {cmd.description}"
                )
            return True

        try:
            await cmd.handler(context, args)
            return True

        except Exception as e:
            logger.error(f"Error executing command {cmd_name}: {e}")
            if context.terminal_ui:
                context.terminal_ui.print_error(f"Command failed: {str(e)}")

            return True

    def get_all_commands(self) -> List[Command]:
        """
        Get list of all unique commands (excluding aliases).

        Returns:
            List of unique Command objects sorted by name
        """
        seen = set()
        commands = []

        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                commands.append(cmd)

        return sorted(commands, key=lambda c: c.name)

    # ==================== Built-in Command Handlers ====================

    async def _cmd_help(self, ctx: CommandContext, args: str):
        """
        Handle /help command.

        Displays comprehensive help information including available commands,
        their descriptions, and usage tips.
        """
        if ctx.terminal_ui:
            help_panel = OutputFormatter.format_help_text()
            ctx.terminal_ui.print_rich(help_panel)

    async def _cmd_status(self, ctx: CommandContext, args: str):
        """
        Handle /status command.

        Displays current system resource usage including CPU, memory, disk,
        and uptime information.
        """
        if not ctx.terminal_ui:
            return

        try:
            health_data = {}
            if hasattr(ctx.terminal_ui, 'health_monitor') and ctx.terminal_ui.health_monitor:
                health_data = ctx.terminal_ui.health_monitor.get_health_summary()
                health_data = health_data.get('current_status', {})

            import time
            if hasattr(ctx.terminal_ui, '_start_time'):
                uptime_seconds = time.time() - ctx.terminal_ui._start_time
                hours, remainder = divmod(int(uptime_seconds), 3600)
                minutes, seconds = divmod(remainder, 60)
                health_data['uptime'] = f"{hours}h {minutes}m {seconds}s"

            status_panel = OutputFormatter.format_system_status(health_data)
            ctx.terminal_ui.print_rich(status_panel)

        except Exception as e:
            logger.error(f"Failed to get system status: {e}")
            ctx.terminal_ui.print_error(f"Failed to get status: {e}")

    async def _cmd_tasks(self, ctx: CommandContext, args: str):
        """
        Handle /tasks command.

        Lists all active download tasks with their status, progress,
        and download speed information.
        """
        if not ctx.terminal_ui:
            return

        tasks = []
        if hasattr(ctx.terminal_ui, 'download_service') and ctx.terminal_ui.download_service:
            tasks_dict = getattr(ctx.terminal_ui.download_service, '_active_downloads', {})
            tasks = [
                {
                    "id": task_id,
                    "status": "downloading" if not task.done() else "completed",
                    "title": f"Task-{task_id}",
                    "progress": 0,
                    "speed": "-"
                }
                for task_id, task in tasks_dict.items()
            ]

        if not tasks:
            ctx.terminal_ui.print_info("No active download tasks")
        else:
            task_table = OutputFormatter.format_task_table(tasks)
            ctx.terminal_ui.print_rich(task_table)

    async def _cmd_cancel(self, ctx: CommandContext, args: str):
        """
        Handle /cancel command.

        Cancels a specific download task by ID and publishes
        a cancellation event through the event bus.
        """
        if not ctx.terminal_ui:
            return

        task_id = args.strip()

        if not task_id:
            ctx.terminal_ui.print_warning("Usage: /cancel <task_id>")
            return

        cancelled = False
        if hasattr(ctx.terminal_ui, 'download_service') and ctx.terminal_ui.download_service:
            cancelled = ctx.terminal_ui.download_service.cancel_download(task_id)

        if cancelled:
            ctx.terminal_ui.print_success(f"Task {task_id} cancelled")

            if ctx.event_bus:
                await ctx.event_bus.publish("download.cancelled", {
                    "task_id": task_id,
                    "source": "terminal"
                })
        else:
            ctx.terminal_ui.print_warning(f"Task {task_id} not found or cannot be cancelled")

    async def _cmd_storage(self, ctx: CommandContext, args: str):
        """
        Handle /storage command.

        Displays storage service status including Nextcloud/WebDAV
        connection information and local storage usage details.
        """
        if not ctx.terminal_ui:
            return

        try:
            storage_info = {}
            if hasattr(ctx.terminal_ui, 'storage_service') and ctx.terminal_ui.storage_service:
                storage_info = ctx.terminal_ui.storage_service.get_storage_info()

            storage_panel = OutputFormatter.format_storage_status(storage_info)
            ctx.terminal_ui.print_rich(storage_panel)

        except Exception as e:
            logger.error(f"Failed to get storage info: {e}")
            ctx.terminal_ui.print_error(f"Failed to get storage info: {e}")

    async def _cmd_log(self, ctx: CommandContext, args: str):
        """
        Handle /log command.

        Sets the global logging level to one of DEBUG, INFO, WARNING, or ERROR.
        Validates the input level before applying changes.
        """
        if not ctx.terminal_ui:
            return

        level = args.strip().upper()
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]

        if level not in valid_levels:
            ctx.terminal_ui.print_warning(
                f"Invalid log level: {level}. Valid levels: {', '.join(valid_levels)}"
            )
            return

        try:
            root_logger = logging.getLogger()
            root_logger.setLevel(getattr(logging, level))
            logger.info(f"Log level changed to {level} via /log command")
            ctx.terminal_ui.print_success(f"Log level set to {level}")
        except Exception as e:
            logger.error(f"Failed to set log level: {e}")
            ctx.terminal_ui.print_error(f"Failed to set log level: {e}")

    async def _cmd_clear(self, ctx: CommandContext, args: str):
        """
        Handle /clear command.

        Clears the main content area and console display.
        """
        if ctx.terminal_ui and hasattr(ctx.terminal_ui, 'main_content'):
            ctx.terminal_ui.main_content.clear()
            ctx.terminal_ui.console.clear()

    async def _cmd_exit(self, ctx: CommandContext, args: str):
        """
        Handle /exit command.

        Initiates graceful shutdown by publishing a shutdown event
        through the event bus and setting the running flag to False.
        """
        if ctx.terminal_ui:
            ctx.terminal_ui.print_info("Shutting down...")

        if ctx.event_bus:
            await ctx.event_bus.publish("system.shutdown_requested", {
                "source": "terminal",
                "reason": "user_exit_command"
            })

        if ctx.terminal_ui:
            ctx.terminal_ui.running = False
