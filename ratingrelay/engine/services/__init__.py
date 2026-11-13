"""What a relay run needs from a service, expressed without a service in it.

A :class:`Source` produces tracks. A :class:`Target` decides what it would write
for a track (a :class:`~ratingrelay.engine.plan.WritePlan`) and hands the
pipeline a way to write it. Neither knows about the database, the API or the
job worker.
"""

from typing import Optional, Protocol

from ratingrelay.engine.plan import WritePlan
from ratingrelay.engine.store import MappingStore, TrackMapping
from ratingrelay.engine.types import Track


class Source(Protocol):
    """A service that holds the ratings we read."""

    service: str

    async def fetch(self) -> list[Track]:
        """Return the tracks currently holding a rating on this service."""


class Target(Protocol):
    """A service we write ratings to."""

    service: str

    async def plan(self, track: Track) -> WritePlan:
        """Match *track* and decide what to write, without writing it."""


class MappedTarget:
    """A target that applies the user's mappings before it does anything else.

    All three targets resolve mappings the same way and only differ in what they
    do next, so the lookup is shared. The cache lasts one run, which is what the
    ``plan()``-per-track loop needs.
    """

    service: str = ""

    def __init__(self, mappings: MappingStore):
        self._mappings = mappings
        self._mapping_cache: Optional[dict[str, TrackMapping]] = None

    async def mappings(self) -> dict[str, TrackMapping]:
        if self._mapping_cache is None:
            self._mapping_cache = await self._mappings.load(self.service)
        return self._mapping_cache

    async def mapped(self, track: Track) -> tuple[str, str, Optional[str]]:
        """The artist, title and recording MBID to write, after any mapping."""
        mapping = (await self.mappings()).get(track.key)
        if mapping is None:
            return track.artist, track.title, track.recording_mbid
        return mapping.artist, mapping.title, mapping.recording_mbid or track.recording_mbid
