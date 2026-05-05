"""
Event Bus for inter-component communication.

Enables loose coupling between Terminal UI, Telegram Handler, 
Download Service and other components through pub/sub pattern.
"""

from typing import Dict, Any, Callable, List, Optional
from dataclasses import dataclass, field
import asyncio
import logging
import threading

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """Represents an event in the system"""
    type: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=lambda: __import__('time').time())
    source: str = ""


class EventBus:
    """
    Simple publish-subscribe event bus for async communication.
    
    Thread-safe implementation supporting both sync and async handlers.
    
    Usage:
        bus = EventBus()
        
        # Subscribe to events
        bus.subscribe("download.started", my_handler)
        
        # Publish events
        await bus.publish("download.started", {"url": "..."})
    """
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.RLock()  # Reentrant lock for nested calls
        
    def subscribe(self, event_type: str, handler: Callable) -> None:
        """
        Subscribe to an event type.
        
        Args:
            event_type: Event type string (e.g., "download.started")
            handler: Callback function (sync or async)
        """
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            
            if handler not in self._subscribers[event_type]:
                self._subscribers[event_type].append(handler)
                logger.debug(f"Subscribed handler to '{event_type}'")
    
    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """Unsubscribe a handler from an event type."""
        with self._lock:
            if event_type in self._subscribers:
                try:
                    self._subscribers[event_type].remove(handler)
                    logger.debug(f"Unsubscribed handler from '{event_type}'")
                except ValueError:
                    pass
    
    async def publish(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> int:
        """
        Publish an event asynchronously.
        
        Notifies all subscribers. Supports both sync and async handlers.
        
        Args:
            event_type: Event type string
            data: Event payload dictionary
            
        Returns:
            Number of handlers notified
        """
        event = Event(type=event_type, data=data or {})
        handlers_copy = self._get_handlers(event_type)
        
        if not handlers_copy:
            logger.debug(f"No subscribers for '{event_type}'")
            return 0
        
        logger.debug(f"Publishing '{event_type}' to {len(handlers_copy)} handlers")
        
        notified_count = 0
        for handler in handlers_copy:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    # Run sync handlers in executor to avoid blocking
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, handler, event)
                
                notified_count += 1
                
            except Exception as e:
                logger.error(f"Error in event handler for '{event_type}': {e}")
        
        return notified_count
    
    def publish_sync(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> int:
        """
        Publish an event synchronously (for non-async contexts).
        
        Returns:
            Number of handlers notified
        """
        event = Event(type=event_type, data=data or {})
        handlers_copy = self._get_handlers(event_type)
        
        notified_count = 0
        for handler in handlers_copy:
            try:
                handler(event)
                notified_count += 1
            except Exception as e:
                logger.error(f"Error in sync handler for '{event_type}': {e}")
        
        return notified_count
    
    def _get_handlers(self, event_type: str) -> List[Callable]:
        """Get a copy of handlers list for an event type (thread-safe)."""
        with self._lock:
            return list(self._subscribers.get(event_type, []))
    
    def get_subscriber_count(self, event_type: str) -> int:
        """Get number of subscribers for an event type."""
        with self._lock:
            return len(self._subscribers.get(event_type, []))
    
    def clear_all(self) -> None:
        """Remove all subscribers (useful for testing)."""
        with self._lock:
            self._subscribers.clear()


# Global event bus instance
_global_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get or create the global event bus instance."""
    global _global_event_bus
    if _global_event_bus is None:
        _global_event_bus = EventBus()
    return _global_event_bus


# Standard event types used across the application
class Events:
    """Constants for standard event types."""
    
    # Download events
    DOWNLOAD_STARTED = "download.started"
    DOWNLOAD_PROGRESS = "download.progress"
    DOWNLOAD_COMPLETED = "download.completed"
    DOWNLOAD_FAILED = "download.failed"
    DOWNLOAD_CANCELLED = "download.cancelled"
    
    # System events
    STATUS_UPDATE = "system.status_update"
    LOG_MESSAGE = "system.log_message"
    SHUTDOWN_REQUESTED = "system.shutdown_requested"
    
    # Telegram events
    TELEGRAM_MESSAGE_RECEIVED = "telegram.message_received"
    TELEGRAM_COMMAND_RECEIVED = "telegram.command_received"
    
    # Storage events
    STORAGE_UPLOAD_STARTED = "storage.upload_started"
    STORAGE_UPLOAD_COMPLETED = "storage.upload_completed"
    STORAGE_UPLOAD_FAILED = "storage.upload_failed"
    
    # Terminal events
    TERMINAL_INPUT_RECEIVED = "terminal.input_received"
    TERMINAL_COMMAND_EXECUTED = "terminal.command_executed"
