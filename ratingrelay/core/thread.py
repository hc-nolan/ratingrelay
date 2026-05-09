"""
Async helper for running synchronous (blocking) callables in a thread-pool executor.
"""

import asyncio
from typing import Callable, TypeVar

T = TypeVar("T")


async def run_sync(fn: Callable[[], T]) -> T:
    """Run a synchronous callable in the default thread-pool executor."""
    return await asyncio.get_running_loop().run_in_executor(None, fn)
