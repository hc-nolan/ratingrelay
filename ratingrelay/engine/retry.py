"""Bounded retry for the engine's network calls.

The relay talks to three services it does not control, and a refused connection or
a timeout says nothing about the relay's decisions. Retrying is safe here because
every call the engine makes is idempotent — setting the same love twice is setting
it once, and a fetch is a read.

The rule is deliberately about *transport*, not about text: ``OSError`` (which is
what ``requests`` and its wrappers raise for connection trouble and timeouts) is
retried; a ``ValueError`` is a decision the engine made and is never retried.
"""

import asyncio
from typing import Awaitable, Callable, TypeVar

from ratingrelay.settings import logger

#: How many times one network call is tried before its failure is believed.
ATTEMPTS = 3

#: First backoff, doubled each retry. A blip is over in a second or two; a service
#: that is really down should not hold a run up for minutes.
BACKOFF_SECONDS = 0.5

_T = TypeVar("_T")


async def attempt(call: Callable[[], Awaitable[_T]], what: str) -> _T:
    """Call and await *call*, retrying a transport failure with a doubling backoff.

    It takes a callable, not a coroutine: a coroutine that raised cannot be awaited
    again, so each attempt needs a fresh one.
    """
    for attempt_number in range(1, ATTEMPTS + 1):
        try:
            return await call()
        except OSError as exc:
            if attempt_number == ATTEMPTS:
                raise
            delay = BACKOFF_SECONDS * 2 ** (attempt_number - 1)
            logger.warning(
                "%s: %s — retrying in %.1fs (attempt %d of %d)",
                what,
                exc,
                delay,
                attempt_number + 1,
                ATTEMPTS,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
