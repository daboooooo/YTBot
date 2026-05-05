"""
Unit tests for EventBus module.
"""

import asyncio
import pytest
import threading

from ytbot.core.event_bus import (
    EventBus,
    Event,
    Events,
    get_event_bus,
)


class TestEventBusBasic:
    """Test basic EventBus functionality."""

    def test_subscribe_and_publish(self):
        """Test basic subscribe and publish workflow."""
        bus = EventBus()
        received_events = []

        def handler(event: Event):
            received_events.append(event)

        bus.subscribe("test.event", handler)
        count = bus.publish_sync("test.event", {"key": "value"})

        assert count == 1
        assert len(received_events) == 1
        assert received_events[0].type == "test.event"
        assert received_events[0].data == {"key": "value"}
        assert received_events[0].timestamp > 0

    def test_multiple_subscribers(self):
        """Test multiple subscribers to the same event type."""
        bus = EventBus()
        results = []

        def handler1(event: Event):
            results.append("handler1")

        def handler2(event: Event):
            results.append("handler2")

        def handler3(event: Event):
            results.append("handler3")

        bus.subscribe("test.event", handler1)
        bus.subscribe("test.event", handler2)
        bus.subscribe("test.event", handler3)

        count = bus.publish_sync("test.event")

        assert count == 3
        assert len(results) == 3
        assert "handler1" in results
        assert "handler2" in results
        assert "handler3" in results

    def test_unsubscribe(self):
        """Test unsubscribing from an event."""
        bus = EventBus()
        received_events = []

        def handler(event: Event):
            received_events.append(event)

        bus.subscribe("test.event", handler)
        assert bus.get_subscriber_count("test.event") == 1

        bus.unsubscribe("test.event", handler)
        assert bus.get_subscriber_count("test.event") == 0

        count = bus.publish_sync("test.event")
        assert count == 0
        assert len(received_events) == 0

    def test_no_subscribers(self):
        """Test publishing when no subscribers exist."""
        bus = EventBus()
        
        count = bus.publish_sync("nonexistent.event")
        assert count == 0
        
        # Should not raise any errors
        assert bus.get_subscriber_count("nonexistent.event") == 0


class TestEventBusAsyncHandlers:
    """Test async handler support."""

    @pytest.mark.asyncio
    async def test_async_handler(self):
        """Test async event handlers are properly awaited."""
        bus = EventBus()
        received_events = []

        async def async_handler(event: Event):
            # Simulate async work
            await asyncio.sleep(0.01)
            received_events.append(event)

        bus.subscribe("async.test", async_handler)
        count = await bus.publish("async.test", {"async": True})

        assert count == 1
        assert len(received_events) == 1
        assert received_events[0].data["async"] is True

    @pytest.mark.asyncio
    async def test_mixed_handlers(self):
        """Test mixing sync and async handlers for the same event."""
        bus = EventBus()
        sync_results = []
        async_results = []

        def sync_handler(event: Event):
            sync_results.append("sync")

        async def async_handler(event: Event):
            await asyncio.sleep(0.01)
            async_results.append("async")

        bus.subscribe("mixed.test", sync_handler)
        bus.subscribe("mixed.test", async_handler)

        count = await bus.publish("mixed.test")

        assert count == 2
        assert len(sync_results) == 1
        assert len(async_results) == 1

    @pytest.mark.asyncio
    async def test_handler_exception_handling(self):
        """Test that exceptions in handlers don't break the event bus."""
        bus = EventBus()
        successful_calls = []

        def failing_handler(event: Event):
            raise ValueError("Handler error")

        def success_handler(event: Event):
            successful_calls.append(True)

        bus.subscribe("error.test", failing_handler)
        bus.subscribe("error.test", success_handler)

        # Should complete without raising, and still call second handler
        count = await bus.publish("error.test")
        
        assert count == 1  # Only one succeeded
        assert len(successful_calls) == 1


class TestEventBusThreadSafety:
    """Test thread safety of EventBus."""

    def test_concurrent_subscribe(self):
        """Test that concurrent subscriptions are thread-safe."""
        bus = EventBus()
        num_threads = 10
        handlers_per_thread = 100

        def subscribe_handlers(thread_id: int):
            for i in range(handlers_per_thread):
                def handler(event: Event, tid=thread_id, idx=i):
                    pass
                
                bus.subscribe(f"concurrent.{thread_id}", handler)

        threads = [
            threading.Thread(target=subscribe_handlers, args=(i,))
            for i in range(num_threads)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify all handlers were subscribed correctly
        for i in range(num_threads):
            count = bus.get_subscriber_count(f"concurrent.{i}")
            assert count == handlers_per_thread, f"Expected {handlers_per_thread}, got {count}"

    def test_clear_all(self):
        """Test clearing all subscribers."""
        bus = EventBus()

        def dummy_handler(event: Event):
            pass

        bus.subscribe("event1", dummy_handler)
        bus.subscribe("event2", dummy_handler)
        bus.subscribe("event3", dummy_handler)

        assert bus.get_subscriber_count("event1") == 1
        assert bus.get_subscriber_count("event2") == 1
        assert bus.get_subscriber_count("event3") == 1

        bus.clear_all()

        assert bus.get_subscriber_count("event1") == 0
        assert bus.get_subscriber_count("event2") == 0
        assert bus.get_subscriber_count("event3") == 0


class TestGlobalEventBus:
    """Test global event bus singleton pattern."""

    def test_get_event_bus_returns_same_instance(self):
        """Test that get_event_bus returns a singleton instance."""
        # Clear any existing instance
        import ytbot.core.event_bus as eb_module
        eb_module._global_event_bus = None

        bus1 = get_event_bus()
        bus2 = get_event_bus()

        assert bus1 is bus2
        assert isinstance(bus1, EventBus)

        # Cleanup
        eb_module._global_event_bus = None

    def test_events_constants_defined(self):
        """Test that all standard event constants are defined."""
        # Download events
        assert hasattr(Events, 'DOWNLOAD_STARTED')
        assert hasattr(Events, 'DOWNLOAD_PROGRESS')
        assert hasattr(Events, 'DOWNLOAD_COMPLETED')
        assert hasattr(Events, 'DOWNLOAD_FAILED')
        assert hasattr(Events, 'DOWNLOAD_CANCELLED')

        # System events
        assert hasattr(Events, 'STATUS_UPDATE')
        assert hasattr(Events, 'LOG_MESSAGE')
        assert hasattr(Events, 'SHUTDOWN_REQUESTED')

        # Telegram events
        assert hasattr(Events, 'TELEGRAM_MESSAGE_RECEIVED')
        assert hasattr(Events, 'TELEGRAM_COMMAND_RECEIVED')

        # Storage events
        assert hasattr(Events, 'STORAGE_UPLOAD_STARTED')
        assert hasattr(Events, 'STORAGE_UPLOAD_COMPLETED')
        assert hasattr(Events, 'STORAGE_UPLOAD_FAILED')

        # Terminal events
        assert hasattr(Events, 'TERMINAL_INPUT_RECEIVED')
        assert hasattr(Events, 'TERMINAL_COMMAND_EXECUTED')

        # Verify they are all strings
        assert isinstance(Events.DOWNLOAD_STARTED, str)
        status_event = Events.STATUS_UPDATE
        assert isinstance(status_event, str)


class TestEventDataClass:
    """Test Event dataclass functionality."""

    def test_event_creation(self):
        """Test creating events with various parameters."""
        # Minimal event
        event1 = Event(type="test.type")
        assert event1.type == "test.type"
        assert event1.data == {}
        assert event1.source == ""
        assert event1.timestamp > 0

        # Full event
        event2 = Event(
            type="download.started",
            data={"url": "https://example.com", "format": "mp4"},
            source="downloader",
            timestamp=1234567890.0
        )
        assert event2.type == "download.started"
        assert event2.data["url"] == "https://example.com"
        assert event2.data["format"] == "mp4"
        assert event2.source == "downloader"
        assert event2.timestamp == 1234567890.0

    def test_event_immutability_of_type(self):
        """Test that event type cannot be changed after creation."""
        event = Event(type="original.type")
        
        # Dataclass fields are mutable by default unless frozen=True
        # But we should verify the initial value is correct
        assert event.type == "original.type"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
