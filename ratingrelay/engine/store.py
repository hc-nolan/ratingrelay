"""Ports the pipeline needs from the world outside it.

The engine never touches a session: a caller supplies a sync store that records
what was written, a mapping store for user overrides, and optionally a progress
sink. In-memory implementations live here too, so tests can drive the pipeline
without a database.
"""

from dataclasses import dataclass, field
from typing import Optional, Protocol

from ratingrelay.engine.types import Track, TrackOutcome


@dataclass(frozen=True, slots=True)
class SyncedTrack:
    """A write this tool made, as recorded after the fact.

    ``intent`` is what makes a later run able to notice drift: comparing it with
    the intent the source now implies is the whole of the reconcile decision.
    ``target_key`` is the identity we wrote to, in whatever form that target
    uses (a rating key, an artist/title pair, a recording MBID) — the engine
    treats it as opaque and hands it back to the same target to undo.
    """

    key: str
    recording_mbid: Optional[str] = None
    intent: Optional[str] = None
    rating: Optional[float] = None
    target_key: Optional[str] = None

    @property
    def track(self) -> Track:
        """A display-only track recovered from the normalized key.

        The record stores the key and nothing else identifying the track, so the
        artist and title here are the normalized forms — lowercase, no accents.
        That is good enough to label a reverted row and not good enough to write
        with, which is why nothing writes from it.
        """
        artist, _, title = self.key.partition("||")
        return Track(artist=artist, title=title, recording_mbid=self.recording_mbid)


@dataclass(frozen=True, slots=True)
class TrackMapping:
    """A user's override: "on this target, this source track is that track"."""

    artist: str
    title: str
    recording_mbid: Optional[str] = None


class SyncStore(Protocol):
    """What we wrote, keyed by source and target service."""

    async def load(self, source_service: str, target_service: str) -> dict[str, SyncedTrack]:
        """Return the recorded writes, keyed by normalized track key."""

    async def remember(
        self, source_service: str, target_service: str, synced: SyncedTrack
    ) -> None:
        """Record a write, replacing any earlier record for the same key."""

    async def forget(self, source_service: str, target_service: str, key: str) -> None:
        """Drop the record of a write, because we just undid it."""


class MappingStore(Protocol):
    """User overrides, resolved for one target service."""

    async def load(self, target_service: str) -> dict[str, TrackMapping]:
        """Return ``{normalized_key: mapping}`` with ``all`` overridden by the service."""


class ProgressSink(Protocol):
    """How far along a run is, for a job worker or a CLI progress bar."""

    async def fetch_complete(self, count: int) -> None:
        """Called once, after the source has produced its track list."""

    async def outcome(self, outcome: TrackOutcome) -> None:
        """Called once per track per target, as soon as the outcome is known."""


class NullProgress:
    """The default sink: a headless run does not have to report anything."""

    async def fetch_complete(self, count: int) -> None:
        pass

    async def outcome(self, outcome: TrackOutcome) -> None:
        pass


@dataclass
class MemorySyncStore:
    """A sync store in a dictionary, for tests and for the CLI's dry runs."""

    records: dict[tuple[str, str], dict[str, SyncedTrack]] = field(default_factory=dict)

    async def load(self, source_service: str, target_service: str) -> dict[str, SyncedTrack]:
        return dict(self.records.get((source_service, target_service), {}))

    async def remember(
        self, source_service: str, target_service: str, synced: SyncedTrack
    ) -> None:
        self.records.setdefault((source_service, target_service), {})[synced.key] = synced

    async def forget(self, source_service: str, target_service: str, key: str) -> None:
        self.records.get((source_service, target_service), {}).pop(key, None)


@dataclass
class MemoryMappingStore:
    """A mapping store in a dictionary, for tests."""

    mappings: dict[str, dict[str, TrackMapping]] = field(default_factory=dict)

    async def load(self, target_service: str) -> dict[str, TrackMapping]:
        return dict(self.mappings.get(target_service, {}))
