"""Run blocking service clients without blocking the event loop.

plexapi, pylast and liblistenbrainz are synchronous. The engine wraps them
through this helper rather than leaking ``asyncio`` plumbing into every client.
"""

import asyncio
from typing import Callable, TypeVar

T = TypeVar("T")


async def run_sync(fn: Callable[[], T]) -> T:
    """Run a synchronous callable in the default thread-pool executor."""
    return await asyncio.get_running_loop().run_in_executor(None, fn)
