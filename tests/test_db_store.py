"""The SQL side of the engine's store ports, against an in-memory database.

The pipeline is tested against the in-memory doubles; this is the part that has
to match a real table, so it gets its own tests.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import ratingrelay.models.jobs  # noqa: F401
import ratingrelay.models.mappings  # noqa: F401
import ratingrelay.models.services  # noqa: F401
import ratingrelay.models.sync  # noqa: F401

from ratingrelay.db.store import SqlMappingStore, SqlSyncStore
from ratingrelay.engine.normalize import track_key
from ratingrelay.engine.store import SyncedTrack, TrackMapping
from ratingrelay.models.mappings import TrackMapping as TrackMappingRow
from ratingrelay.models.sync import SyncRecord

TRACK_KEY = track_key("Sigur Rós", "Hoppípolla")


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _rows(db: AsyncSession) -> list[SyncRecord]:
    result = await db.execute(select(SyncRecord))
    return list(result.scalars().all())


async def test_round_trip_keeps_the_intent_and_the_target_key(db):
    store = SqlSyncStore(db)
    await store.remember(
        "plex",
        "lastfm",
        SyncedTrack(
            key=TRACK_KEY,
            recording_mbid="rec-1",
            intent="love",
            rating=None,
            target_key='["Sigur Rós", "Hoppípolla"]',
        ),
    )

    loaded = await store.load("plex", "lastfm")

    assert loaded[TRACK_KEY] == SyncedTrack(
        key=TRACK_KEY,
        recording_mbid="rec-1",
        intent="love",
        rating=None,
        target_key='["Sigur Rós", "Hoppípolla"]',
    )


async def test_a_second_write_replaces_the_first_rather_than_duplicating(db):
    store = SqlSyncStore(db)
    await store.remember("plex", "plex", SyncedTrack(TRACK_KEY, intent="rating:8.0", rating=8.0))
    await store.remember(
        "plex", "plex", SyncedTrack(TRACK_KEY, intent="rating:10.0", rating=10.0, target_key='["1"]')
    )

    rows = await _rows(db)
    loaded = await store.load("plex", "plex")

    assert len(rows) == 1
    assert loaded[TRACK_KEY].intent == "rating:10.0"
    assert loaded[TRACK_KEY].target_key == '["1"]'


async def test_load_is_scoped_to_one_source_and_target_pair(db):
    store = SqlSyncStore(db)
    await store.remember("plex", "lastfm", SyncedTrack(TRACK_KEY, intent="love"))
    await store.remember("lastfm", "plex", SyncedTrack(TRACK_KEY, intent="rating:10.0"))
    await store.remember("plex", "listenbrainz", SyncedTrack(TRACK_KEY, intent="love"))

    assert list(await store.load("plex", "lastfm")) == [TRACK_KEY]
    assert (await store.load("plex", "listenbrainz"))[TRACK_KEY].intent == "love"


async def test_forget_drops_one_record(db):
    store = SqlSyncStore(db)
    await store.remember("plex", "lastfm", SyncedTrack(TRACK_KEY, intent="love"))

    await store.forget("plex", "lastfm", TRACK_KEY)

    assert await store.load("plex", "lastfm") == {}
    assert await _rows(db) == []


async def test_forget_target_drops_only_that_target(db):
    store = SqlSyncStore(db)
    await store.remember("plex", "lastfm", SyncedTrack(TRACK_KEY, intent="love"))
    await store.remember("plex", "listenbrainz", SyncedTrack(TRACK_KEY, intent="love"))

    await store.forget_target("lastfm")

    remaining = await db.execute(select(func.count()).select_from(SyncRecord))
    assert remaining.scalar() == 1
    assert list(await store.load("plex", "listenbrainz")) == [TRACK_KEY]


async def test_mapping_store_prefers_a_service_specific_override(db):
    db.add(
        TrackMappingRow(
            source_normalized_key=TRACK_KEY,
            source_artist="Sigur Rós",
            source_title="Hoppípolla",
            target_service="all",
            mapped_artist="Everything",
            mapped_title="Everything",
        )
    )
    db.add(
        TrackMappingRow(
            source_normalized_key=TRACK_KEY,
            source_artist="Sigur Rós",
            source_title="Hoppípolla",
            target_service="lastfm",
            mapped_artist="Lastfm Only",
            mapped_title="Lastfm Only",
        )
    )
    await db.commit()

    store = SqlMappingStore(db)

    assert (await store.load("lastfm"))[TRACK_KEY] == TrackMapping("Lastfm Only", "Lastfm Only")
    assert (await store.load("plex"))[TRACK_KEY] == TrackMapping("Everything", "Everything")
