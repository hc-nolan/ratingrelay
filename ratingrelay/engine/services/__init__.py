"""What a relay run needs from a service, expressed without a service in it.

A :class:`Source` produces tracks. A :class:`Target` decides what it would write
for a track (a :class:`~ratingrelay.engine.plan.WritePlan`) and hands the
pipeline a way to write it. Neither knows about the database, the API or the
job worker.
"""

from typing import Optional, Protocol

from ratingrelay.engine.plan import WritePlan
from ratingrelay.engine.store import MappingStore, SyncedTrack, TrackMapping
from ratingrelay.engine.types import Track


class Source(Protocol):
    """A service that holds the ratings we read."""

    # The account's ref (``plex``, ``lastfm:old``), not just the kind: it is half
    # of the key the sync store is written under. See ``engine.refs``.
    service: str

    async def fetch(self) -> list[Track]:
        """Return the tracks currently holding a rating on this service."""


class Target(Protocol):
    """A service we write ratings to."""

    # The account's ref (``plex:attic``), see ``Source.service``.
    service: str

    async def plan(self, track: Track) -> WritePlan:
        """Match *track* and decide what to write, without writing it."""

    def undoable(self, record: SyncedTrack) -> None:
        """Check, without writing, that *record* says what we wrote there.

        Must raise ``ValueError`` when it does not: the pipeline then leaves the
        record alone rather than guessing at a rating that may not be ours to
        clear. It exists separately from :meth:`undo` so that a dry run can make
        the same promise a real run keeps.
        """

    async def undo(self, record: SyncedTrack) -> None:
        """Revert a write we recorded, using the record's ``target_key``.

        Must raise ``ValueError`` when the record does not say what we wrote:
        the pipeline then leaves the record alone rather than guessing at a
        rating that may not be ours to clear.
        """


class MappedTarget:
    """A target that applies the user's mappings before it does anything else.

    All four targets resolve mappings the same way and only differ in what they
    do next, so the lookup is shared. The cache lasts one run, which is what the
    ``plan()``-per-track loop needs.

    ``service`` is the target's ref, set by the constructor of each concrete
    target: mappings are looked up for that account, then its kind, then ``all``
    (:func:`ratingrelay.engine.refs.mapping_scopes`), so a mapping written
    before a service had a second account still applies to it.
    """

    service: str = ""

    def __init__(self, mappings: MappingStore):
        self._mappings = mappings
        self._mapping_cache: Optional[dict[str, TrackMapping]] = None

    async def mappings(self) -> dict[str, TrackMapping]:
        if self._mapping_cache is None:
            self._mapping_cache = await self._mappings.load(self.service)
        return self._mapping_cache

    async def has_mapping(self, track: Track) -> bool:
        return track.key in await self.mappings()

    async def mapped(self, track: Track) -> tuple[str, str, Optional[str]]:
        """The artist, title and recording MBID to write, after any mapping."""
        mapping = (await self.mappings()).get(track.key)
        if mapping is None:
            return track.artist, track.title, track.recording_mbid
        return mapping.artist, mapping.title, mapping.recording_mbid or track.recording_mbid
