#!/usr/bin/env python
"""Quick verification script for EventBus implementation (standalone)."""

import sys
import os

# Add the core directory directly to path to avoid importing the full package
sys.path.insert(0, '/Users/horsenli/Works/ytbot')
sys.path.insert(0, '/Users/horsenli/Works/ytbot/ytbot/core')

# Import directly from the module file
exec(open('/Users/horsenli/Works/ytbot/ytbot/core/event_bus.py').read())

def test_basic():
    """Test basic functionality."""
    bus = EventBus()
    results = []
    
    def handler(event):
        results.append(event.type)
    
    bus.subscribe('test.event', handler)
    count = bus.publish_sync('test.event', {'key': 'value'})
    
    assert count == 1
    assert len(results) == 1
    assert results[0] == 'test.event'
    print("✓ Test 1 passed: Basic subscribe and publish")

def test_multiple_subscribers():
    """Test multiple subscribers."""
    bus = EventBus()
    r = []
    
    def h1(e): r.append(1)
    def h2(e): r.append(2)
    
    bus.subscribe('multi', h1)
    bus.subscribe('multi', h2)
    
    count = bus.publish_sync('multi')
    assert count == 2
    assert len(r) == 2
    print("✓ Test 2 passed: Multiple subscribers")

def test_unsubscribe():
    """Test unsubscribe functionality."""
    bus = EventBus()
    
    def h(e): pass
    
    bus.subscribe('event', h)
    assert bus.get_subscriber_count('event') == 1
    
    bus.unsubscribe('event', h)
    assert bus.get_subscriber_count('event') == 0
    
    count = bus.publish_sync('event')
    assert count == 0
    print("✓ Test 3 passed: Unsubscribe")

def test_no_subscribers():
    """Test publishing with no subscribers."""
    bus = EventBus()
    count = bus.publish_sync('nonexistent')
    assert count == 0
    print("✓ Test 4 passed: No subscribers")

def test_event_dataclass():
    """Test Event dataclass."""
    e = Event(
        type='download.started',
        data={'url': 'https://example.com'},
        source='downloader'
    )
    
    assert e.type == 'download.started'
    assert e.data['url'] == 'https://example.com'
    assert e.source == 'downloader'
    assert e.timestamp > 0
    print("✓ Test 5 passed: Event dataclass")

def test_events_constants():
    """Test Events constants are defined."""
    assert hasattr(Events, 'DOWNLOAD_STARTED')
    assert hasattr(Events, 'TELEGRAM_MESSAGE_RECEIVED')
    assert isinstance(Events.DOWNLOAD_STARTED, str)
    print("✓ Test 6 passed: Events constants defined")

def test_clear_all():
    """Test clearing all subscribers."""
    bus = EventBus()
    
    def dummy(e): pass
    
    bus.subscribe('a', dummy)
    bus.subscribe('b', dummy)
    
    bus.clear_all()
    
    assert bus.get_subscriber_count('a') == 0
    assert bus.get_subscriber_count('b') == 0
    print("✓ Test 7 passed: Clear all subscribers")

if __name__ == '__main__':
    try:
        test_basic()
        test_multiple_subscribers()
        test_unsubscribe()
        test_no_subscribers()
        test_event_dataclass()
        test_events_constants()
        test_clear_all()
        
        print("\n" + "="*50)
        print("✅ All tests passed successfully!")
        print("="*50)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
