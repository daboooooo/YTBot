"""
Shared browser manager for Playwright instances.

Provides singleton access to browser instances to prevent resource conflicts
and ensure proper cleanup.
"""

import asyncio
from typing import Optional, Dict, Any, List
from pathlib import Path

from ..core.logger import get_logger

logger = get_logger(__name__)

# Try to import Playwright
try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.warning("Playwright not installed. Browser features will be disabled.")


class BrowserManager:
    """
    Singleton browser manager for sharing Playwright instances across handlers.
    
    Features:
    - Single browser instance shared across all handlers
    - Automatic resource cleanup
    - Connection pooling for contexts
    - Thread-safe operations
    """
    
    _instance: Optional['BrowserManager'] = None
    _instance_lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._contexts: List[BrowserContext] = []
        self._lock = asyncio.Lock()
        self._initialized = True
        self._shutdown = False
        self._ref_count = 0
        
    @classmethod
    async def get_instance(cls) -> 'BrowserManager':
        """Get or create the singleton instance."""
        async with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance
    
    async def initialize(self) -> bool:
        """
        Initialize the shared browser instance.
        
        Returns:
            bool: True if initialization successful
        """
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Playwright not available. Install with: pip install playwright")
            return False
            
        async with self._lock:
            if self._browser is not None:
                logger.debug("Browser already initialized")
                return True
                
            if self._shutdown:
                logger.error("Browser manager has been shut down")
                return False
            
            try:
                logger.info("🌐 Initializing shared browser instance...")
                
                self._playwright = await async_playwright().start()
                
                # Launch browser with anti-detection settings
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-accelerated-2d-canvas',
                        '--no-first-run',
                        '--no-zygote',
                        '--disable-gpu',
                        '--disable-web-security',
                        '--allow-running-insecure-content',
                        '--disable-features=VizDisplayCompositor',
                        '--disable-extensions',
                        '--disable-ipc-flooding-protection',
                        '--disable-background-timer-throttling',
                        '--disable-backgrounding-occluded-windows',
                        '--disable-renderer-backgrounding',
                    ]
                )
                
                self._ref_count = 1
                logger.info("✅ Shared browser instance initialized")
                return True
                
            except Exception as e:
                logger.error(f"❌ Failed to initialize browser: {e}")
                await self._cleanup()
                return False
    
    async def create_context(
        self,
        cookies: Optional[List[Dict[str, Any]]] = None,
        user_agent: Optional[str] = None,
        viewport: Optional[Dict[str, int]] = None
    ) -> Optional[BrowserContext]:
        """
        Create a new browser context.
        
        Args:
            cookies: Optional cookies to add to context
            user_agent: Optional custom user agent
            viewport: Optional viewport settings
            
        Returns:
            BrowserContext or None if failed
        """
        async with self._lock:
            if self._browser is None:
                logger.error("Browser not initialized")
                return None
                
            try:
                # Default settings
                ua = user_agent or (
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                vp = viewport or {'width': 1920, 'height': 1080}
                
                context = await self._browser.new_context(
                    user_agent=ua,
                    viewport=vp,
                    timezone_id='Asia/Shanghai',
                    locale='zh-CN,zh;q=0.9,en;q=0.8',
                    geolocation={'longitude': 121.4737, 'latitude': 31.2304},
                    permissions=['geolocation']
                )
                
                # Add anti-detection scripts
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined,
                    });
                    
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => ({
                            length: 3,
                            0: { filename: 'internal-pdf-viewer' },
                            1: { filename: 'adsfk-plugin' },
                            2: { filename: 'internal-nacl-plugin' },
                            refresh: () => {},
                        }),
                    });
                    
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['zh-CN', 'zh', 'en'],
                    });
                """)
                
                # Add cookies if provided
                if cookies:
                    await context.add_cookies(cookies)
                    logger.debug(f"Added {len(cookies)} cookies to context")
                
                self._contexts.append(context)
                logger.debug(f"Created new browser context (total: {len(self._contexts)})")
                return context
                
            except Exception as e:
                logger.error(f"❌ Failed to create browser context: {e}")
                return None
    
    async def close_context(self, context: BrowserContext):
        """
        Close a browser context.
        
        Args:
            context: The context to close
        """
        async with self._lock:
            if context in self._contexts:
                try:
                    await context.close()
                    self._contexts.remove(context)
                    logger.debug(f"Closed browser context (remaining: {len(self._contexts)})")
                except Exception as e:
                    logger.warning(f"⚠️ Error closing context: {e}")
    
    async def acquire(self) -> bool:
        """
        Acquire a reference to the browser manager.
        
        Returns:
            bool: True if acquired successfully
        """
        async with self._lock:
            if self._shutdown:
                return False
            self._ref_count += 1
            logger.debug(f"Browser reference acquired (count: {self._ref_count})")
            return True
    
    async def release(self):
        """Release a reference to the browser manager."""
        async with self._lock:
            self._ref_count = max(0, self._ref_count - 1)
            logger.debug(f"Browser reference released (count: {self._ref_count})")
            
            # Auto-shutdown if no more references
            if self._ref_count == 0:
                logger.info("No more references, shutting down browser")
                await self._cleanup()
    
    async def shutdown(self):
        """Force shutdown the browser manager."""
        async with self._lock:
            await self._cleanup()
    
    async def _cleanup(self):
        """Internal cleanup method."""
        self._shutdown = True
        
        # Close all contexts
        for context in self._contexts[:]:
            try:
                await context.close()
            except Exception as e:
                logger.warning(f"⚠️ Error closing context during cleanup: {e}")
        self._contexts.clear()
        
        # Close browser
        if self._browser:
            try:
                await self._browser.close()
                logger.info("🔒 Browser closed")
            except Exception as e:
                logger.warning(f"⚠️ Error closing browser: {e}")
            self._browser = None
        
        # Stop playwright
        if self._playwright:
            try:
                await self._playwright.stop()
                logger.debug("Playwright stopped")
            except Exception as e:
                logger.warning(f"⚠️ Error stopping playwright: {e}")
            self._playwright = None
        
        self._ref_count = 0
    
    @property
    def is_initialized(self) -> bool:
        """Check if browser is initialized."""
        return self._browser is not None and not self._shutdown
    
    @property
    def reference_count(self) -> int:
        """Get current reference count."""
        return self._ref_count


# Global instance accessor
_browser_manager: Optional[BrowserManager] = None


async def get_browser_manager() -> BrowserManager:
    """Get the global browser manager instance."""
    global _browser_manager
    if _browser_manager is None:
        _browser_manager = await BrowserManager.get_instance()
    return _browser_manager


async def initialize_browser() -> bool:
    """Initialize the shared browser."""
    manager = await get_browser_manager()
    return await manager.initialize()


async def shutdown_browser():
    """Shutdown the shared browser."""
    global _browser_manager
    if _browser_manager:
        await _browser_manager.shutdown()
        _browser_manager = None
