"""
Async utilities for YTBot

Provides async wrappers and utilities for common operations.
"""

import asyncio
import functools
from typing import Callable, Any, TypeVar, Optional, Coroutine
from concurrent.futures import ThreadPoolExecutor
import time

T = TypeVar('T')

# Global thread pool for running sync code in async context
_thread_pool: Optional[ThreadPoolExecutor] = None


def get_thread_pool() -> ThreadPoolExecutor:
    """Get or create global thread pool"""
    global _thread_pool
    if _thread_pool is None:
        _thread_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="ytbot_worker")
    return _thread_pool


def shutdown_thread_pool() -> None:
    """Shutdown global thread pool"""
    global _thread_pool
    if _thread_pool is not None:
        _thread_pool.shutdown(wait=True)
        _thread_pool = None


async def run_in_thread(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    Run a synchronous function in a thread pool.
    
    Args:
        func: Function to run
        *args: Positional arguments
        **kwargs: Keyword arguments
        
    Returns:
        Function result
    """
    loop = asyncio.get_running_loop()
    pool = get_thread_pool()
    
    # Create partial function with kwargs
    if kwargs:
        func = functools.partial(func, **kwargs)
    
    return await loop.run_in_executor(pool, func, *args)


async def run_with_timeout(
    coro: Coroutine[Any, Any, T],
    timeout: float,
    timeout_message: str = "Operation timed out"
) -> T:
    """
    Run a coroutine with timeout.
    
    Args:
        coro: Coroutine to run
        timeout: Timeout in seconds
        timeout_message: Message for timeout exception
        
    Returns:
        Coroutine result
        
    Raises:
        asyncio.TimeoutError: If operation times out
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        raise asyncio.TimeoutError(timeout_message)


async def retry_with_backoff(
    func: Callable[..., Coroutine[Any, Any, T]],
    *args: Any,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    retry_exceptions: tuple = (Exception,),
    **kwargs: Any
) -> T:
    """
    Retry an async function with exponential backoff.
    
    Args:
        func: Async function to retry
        *args: Positional arguments
        max_retries: Maximum number of retries
        initial_delay: Initial delay between retries
        max_delay: Maximum delay between retries
        backoff_factor: Factor to increase delay
        retry_exceptions: Exceptions to retry on
        **kwargs: Keyword arguments
        
    Returns:
        Function result
        
    Raises:
        Exception: If all retries fail
    """
    delay = initial_delay
    last_exception: Optional[Exception] = None
    
    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except retry_exceptions as e:
            last_exception = e
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay = min(delay * backoff_factor, max_delay)
            else:
                break
    
    raise last_exception or Exception("Retry failed")


class AsyncContextManager:
    """Base class for async context managers"""
    
    async def __aenter__(self) -> 'AsyncContextManager':
        return self
    
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
    
    async def close(self) -> None:
        """Override to implement cleanup"""
        pass


class RateLimiter:
    """Async rate limiter using token bucket algorithm"""
    
    def __init__(self, rate: float, burst: int = 1):
        """
        Initialize rate limiter.
        
        Args:
            rate: Rate limit (tokens per second)
            burst: Maximum burst size
        """
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> None:
        """Acquire a token, waiting if necessary"""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_update = now
            
            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0
                self.last_update = time.monotonic()
            else:
                self.tokens -= 1


def async_retry(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Decorator to retry async functions.
    
    Args:
        max_retries: Maximum number of retries
        initial_delay: Initial delay between retries
        backoff_factor: Factor to increase delay
        exceptions: Exceptions to retry on
        
    Returns:
        Decorator function
    """
    def decorator(func: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await retry_with_backoff(
                func, *args,
                max_retries=max_retries,
                initial_delay=initial_delay,
                backoff_factor=backoff_factor,
                retry_exceptions=exceptions,
                **kwargs
            )
        return wrapper
    return decorator


async def gather_with_concurrency(
    limit: int,
    *coros: Coroutine[Any, Any, T]
) -> list[T]:
    """
    Gather coroutines with concurrency limit.
    
    Args:
        limit: Maximum concurrent coroutines
        *coros: Coroutines to run
        
    Returns:
        List of results
    """
    semaphore = asyncio.Semaphore(limit)
    
    async def sem_coro(coro: Coroutine[Any, Any, T]) -> T:
        async with semaphore:
            return await coro
    
    return await asyncio.gather(*(sem_coro(c) for c in coros))


class AsyncTimer:
    """Async timer for periodic tasks"""
    
    def __init__(self, interval: float, callback: Callable[..., Coroutine[Any, Any, Any]]):
        """
        Initialize timer.
        
        Args:
            interval: Interval in seconds
            callback: Async callback function
        """
        self.interval = interval
        self.callback = callback
        self._task: Optional[asyncio.Task] = None
        self._running = False
    
    async def start(self) -> None:
        """Start the timer"""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run())
    
    async def stop(self) -> None:
        """Stop the timer"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
    
    async def _run(self) -> None:
        """Run the timer loop"""
        while self._running:
            try:
                await self.callback()
            except Exception as e:
                # Log error but continue
                import logging
                logging.getLogger(__name__).error(f"Timer callback error: {e}")
            
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
