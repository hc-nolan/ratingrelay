"""RR-6: a reset must not cost the lookups that filled the cache.

The recording MBID used to live only on the ``syncrecord`` a reset deletes, so
reset-then-rerun was a cold run — ~16 minutes for 361 tracks, against 35 seconds
warm. These tests pin the property that makes the difference: after a reset, the
next run resolves nothing over the network.
"""

from typing import Optional

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import ratingrelay.models.mbids  # noqa: F401

from ratingrelay.db.store import SqlMbidCache
from ratingrelay.engine import pipeline
from ratingrelay.engine.services.listenbrainz import ListenBrainzTarget
from ratingrelay.engine.services.musicbrainz import Resolution
from ratingrelay.engine.store import (
    MemoryMappingStore,
    MemoryMbidCache,
    MemorySyncStore,
    SyncedTrack,
    TrackMapping,
)
from ratingrelay.engine.types import OutcomeStatus, Reason, Track


class FakeListenBrainzClient:
    def __init__(self, fail_with: Optional[str] = None):
        self._fail_with = fail_with
        self.submissions: list[tuple[int, str]] = []

    async def submit(self, score: int, mbid: str) -> None:
        if self._fail_with is not None:
            raise RuntimeError(self._fail_with)
        self.submissions.append((score, mbid))


class CountingResolver:
    def __init__(self, mbid: str = "looked-up"):
        self.mbid = mbid
        self.calls = 0

    async def __call__(self, track_mbid, artist, title):  # noqa: ARG002 - the resolver port
        self.calls += 1
        return Resolution(self.mbid)


def _target(client, resolver, *, cache=None, mappings=None) -> ListenBrainzTarget:
    return ListenBrainzTarget(
        client, mappings or MemoryMappingStore(), resolver, 1, cache=cache
    )


async def test_a_reset_does_not_send_the_next_run_back_to_musicbrainz():
    """The whole point: reset drops write records, and the MBIDs stay learned."""
    client, resolver, cache, store = (
        FakeListenBrainzClient(),
        CountingResolver("rec-1"),
        MemoryMbidCache(),
        MemorySyncStore(),
    )
    track = Track("Artist", "Song")

    first = await pipeline.run_tracks(
        [track],
        [_target(client, resolver, cache=cache)],
        source_service="plex",
        sync_store=store,
    )

    assert first.outcomes[0].status is OutcomeStatus.WRITTEN
    assert resolver.calls == 1
    assert cache.entries[track.key] == "rec-1"

    # Reset: every record we wrote to ListenBrainz is gone. The cache is not.
    store.records[("plex", "listenbrainz")].clear()

    second = await pipeline.run_tracks(
        [track],
        [_target(client, resolver, cache=cache)],
        source_service="plex",
        sync_store=store,
    )

    assert second.outcomes[0].status is OutcomeStatus.WRITTEN
    assert resolver.calls == 1, "the second run must not resolve anything"
    assert client.submissions == [(1, "rec-1"), (1, "rec-1")]


async def test_without_a_cache_a_reset_is_a_cold_run():
    """The behaviour the cache exists to prevent — pinned so it cannot drift back."""
    client, resolver, store = FakeListenBrainzClient(), CountingResolver("rec-1"), MemorySyncStore()
    track = Track("Artist", "Song")

    await pipeline.run_tracks(
        [track], [_target(client, resolver)], source_service="plex", sync_store=store
    )
    store.records[("plex", "listenbrainz")].clear()
    await pipeline.run_tracks(
        [track], [_target(client, resolver)], source_service="plex", sync_store=store
    )

    assert resolver.calls == 2


async def test_a_mapping_outranks_a_cached_mbid():
    """A human correction must keep working after the old answer is cached."""
    client, resolver, cache = FakeListenBrainzClient(), CountingResolver("network"), MemoryMbidCache()
    track = Track("Artist", "Song")
    cache.entries[track.key] = "from-the-cache"
    mappings = MemoryMappingStore(
        {"listenbrainz": {track.key: TrackMapping("Artist", "Song", "hand-picked")}}
    )

    report = await pipeline.run_tracks(
        [track],
        [_target(client, resolver, cache=cache, mappings=mappings)],
        source_service="plex",
        sync_store=MemorySyncStore(),
    )

    assert report.outcomes[0].status is OutcomeStatus.WRITTEN
    assert client.submissions == [(1, "hand-picked")]
    assert resolver.calls == 0


async def test_a_dry_run_reads_the_cache_but_does_not_fill_it():
    client, resolver, cache = FakeListenBrainzClient(), CountingResolver("net"), MemoryMbidCache()
    known, unknown = Track("Artist", "Known"), Track("Artist", "Unknown")
    cache.entries[known.key] = "rec-known"

    report = await pipeline.run_tracks(
        [known, unknown],
        [_target(client, resolver, cache=cache)],
        source_service="plex",
        sync_store=MemorySyncStore(),
        dry_run=True,
    )

    assert [o.status for o in report.outcomes] == [OutcomeStatus.WRITTEN, OutcomeStatus.WRITTEN]
    assert client.submissions == [], "a dry run writes nothing, including the cache"
    assert cache.entries == {known.key: "rec-known"}


async def test_a_failed_write_teaches_the_cache_nothing():
    client = FakeListenBrainzClient(fail_with="listenbrainz is down")
    cache, track = MemoryMbidCache(), Track("Artist", "Song")

    report = await pipeline.run_tracks(
        [track],
        [_target(client, CountingResolver("rec-1"), cache=cache)],
        source_service="plex",
        sync_store=MemorySyncStore(),
    )

    assert report.outcomes[0].status is OutcomeStatus.FAILED
    assert report.outcomes[0].reason is Reason.WRITE_FAILED
    assert cache.entries == {}


async def test_an_already_synced_track_does_not_consult_the_network():
    """The record's own MBID is enough; the cache is for tracks with no record."""
    client, resolver, cache = FakeListenBrainzClient(), CountingResolver("net"), MemoryMbidCache()
    track = Track("Artist", "Song")
    store = MemorySyncStore()
    await store.remember(
        "plex",
        "listenbrainz",
        SyncedTrack(track.key, recording_mbid="rec-from-record", intent="love"),
    )

    report = await pipeline.run_tracks(
        [track],
        [_target(client, resolver, cache=cache)],
        source_service="plex",
        sync_store=store,
    )

    assert report.outcomes[0].status is OutcomeStatus.SKIPPED
    assert report.outcomes[0].reason is Reason.ALREADY_SYNCED
    assert resolver.calls == 0


# ---------------------------------------------------------------------------
# The SQL side
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def test_the_sql_cache_round_trips_a_mbid(db: AsyncSession):
    cache = SqlMbidCache(db)

    assert await cache.get("artist||song") is None
    await cache.put("artist||song", "Artist", "Song", "rec-1")

    assert await cache.get("artist||song") == "rec-1"


async def test_the_sql_cache_replaces_rather_than_duplicating(db: AsyncSession):
    cache = SqlMbidCache(db)

    await cache.put("artist||song", "Artist", "Song", "rec-1")
    await cache.put("artist||song", "Artist", "Song (Remaster)", "rec-2")

    assert await cache.get("artist||song") == "rec-2"
