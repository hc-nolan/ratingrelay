"""Ports the pipeline needs from the world outside it.

The engine never touches a session: a caller supplies a sync store that records
what was written, a mapping store for user overrides, and optionally a progress
sink. In-memory implementations live here too, so tests can drive the pipeline
without a database.
"""

from dataclasses import dataclass, field
from typing import Optional, Protocol

from ratingrelay.engine.types import TrackOutcome


@dataclass(frozen=True, slots=True)
class SyncedTrack:
    """A write this tool made, as recorded after the fact.

    ``intent`` and the rating on the record are what make a later run able to
    notice that a target has drifted; they are optional until the reconcile
    slice lands the migration for them.
    """

    key: str
    recording_mbid: Optional[str] = None
    intent: Optional[str] = None
    rating: Optional[float] = None


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
        """Record a write so a later run does not repeat it."""

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
