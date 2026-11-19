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
    store = SqlSyncStore(db, "love")
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
    store = SqlSyncStore(db, "love")
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
    store = SqlSyncStore(db, "love")
    await store.remember("plex", "lastfm", SyncedTrack(TRACK_KEY, intent="love"))
    await store.remember("lastfm", "plex", SyncedTrack(TRACK_KEY, intent="rating:10.0"))
    await store.remember("plex", "listenbrainz", SyncedTrack(TRACK_KEY, intent="love"))

    assert list(await store.load("plex", "lastfm")) == [TRACK_KEY]
    assert (await store.load("plex", "listenbrainz"))[TRACK_KEY].intent == "love"


async def test_forget_drops_one_record(db):
    store = SqlSyncStore(db, "love")
    await store.remember("plex", "lastfm", SyncedTrack(TRACK_KEY, intent="love"))

    await store.forget("plex", "lastfm", TRACK_KEY)

    assert await store.load("plex", "lastfm") == {}
    assert await _rows(db) == []


async def test_forget_target_drops_only_that_target(db):
    store = SqlSyncStore(db, "love")
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


def _love(key: str) -> SyncedTrack:
    return SyncedTrack(key=key, recording_mbid=None, intent="love", rating=None, target_key=key)


async def test_relays_on_one_pair_keep_their_records_apart(db):
    """A love relay and a hate relay to the same target never see each other's rows."""
    loves, hates = SqlSyncStore(db, "love"), SqlSyncStore(db, "hate")
    await loves.remember("listenbrainz", "plex", _love("a"))
    await hates.remember("listenbrainz", "plex", _love("b"))

    assert set(await loves.load("listenbrainz", "plex")) == {"a"}
    assert set(await hates.load("listenbrainz", "plex")) == {"b"}

    await loves.forget("listenbrainz", "plex", "b")
    assert set(await hates.load("listenbrainz", "plex")) == {"b"}


async def test_rows_from_before_relays_are_claimed_by_the_first_relay_to_load(db):
    db.add(SyncRecord(source_service="plex", target_service="lastfm", normalized_key="a"))
    db.add(SyncRecord(source_service="plex", target_service="listenbrainz", normalized_key="b"))
    await db.commit()

    assert set(await SqlSyncStore(db, "love").load("plex", "lastfm")) == {"a"}
    assert await SqlSyncStore(db, "hate").load("plex", "lastfm") == {}
    relays = {(row.target_service, row.relay) for row in await _rows(db)}
    assert relays == {("lastfm", "love"), ("listenbrainz", "")}


async def test_an_unscoped_store_refuses_to_write(db):
    with pytest.raises(ValueError):
        await SqlSyncStore(db).remember("plex", "lastfm", _love("a"))


async def test_a_love_relay_and_a_hate_relay_do_not_revert_each_other(db):
    """ListenBrainz loves and hates both into Plex: every run used to undo the other."""
    from ratingrelay.engine import pipeline
    from ratingrelay.engine.services.plex import PlexTarget
    from ratingrelay.engine.store import MemoryMappingStore
    from ratingrelay.engine.types import Candidate, Track

    class Library:
        def __init__(self):
            self.ratings = {}

        async def candidates(self):
            return (
                Candidate("A", "Loved", external_id="1"),
                Candidate("B", "Hated", external_id="2"),
            )

        async def rate(self, key, rating):
            self.ratings[key] = rating

    class Source:
        service = "listenbrainz"

        def __init__(self, tracks):
            self.tracks = tracks

        async def fetch(self):
            return self.tracks

    library = Library()
    runs = [("love", Track("A", "Loved"), 10.0), ("hate", Track("B", "Hated"), 2.0)]
    for relay, track, rating in runs * 2:
        target = PlexTarget(library, MemoryMappingStore(), rating)
        await pipeline.run(Source([track]), [target], sync_store=SqlSyncStore(db, relay))

    assert library.ratings == {"1": 10.0, "2": 2.0}


async def test_a_dry_run_store_shows_the_adoption_without_making_it(db):
    """A dry run must not claim legacy rows: that is a write, and a permanent one.

    A dry run of a hate relay that adopted plex→lastfm's unclaimed rows would hand
    the love relay's writes to the hate relay before either had really run.
    """
    db.add(SyncRecord(source_service="plex", target_service="lastfm", relay="",
                      normalized_key=TRACK_KEY, intent="love", target_key="k"))
    await db.commit()

    preview = await SqlSyncStore(db, "hate", read_only=True).load("plex", "lastfm")

    assert set(preview) == {TRACK_KEY}  # it sees what a real run would adopt
    assert [row.relay for row in await _rows(db)] == [""]  # and claims none of it
    assert set(await SqlSyncStore(db, "love").load("plex", "lastfm")) == {TRACK_KEY}


async def test_a_dry_run_store_prefers_the_claimed_row(db):
    for relay, intent in (("love", "love"), ("", "hate")):
        db.add(SyncRecord(source_service="plex", target_service="lastfm", relay=relay,
                          normalized_key=TRACK_KEY, intent=intent, target_key="k"))
    await db.commit()

    preview = await SqlSyncStore(db, "love", read_only=True).load("plex", "lastfm")

    assert preview[TRACK_KEY].intent == "love"


async def test_known_keys_spans_relays_while_a_load_does_not(db):
    """An exact relay asks this to tell its own pair's writes from the user's."""
    await SqlSyncStore(db, "love").remember(
        "plex", "hyperfix", SyncedTrack(key=TRACK_KEY, intent="love", rating=8.0)
    )

    # Scoped to another relay: it has no records of its own...
    assert await SqlSyncStore(db, "rating").load("plex", "hyperfix") == {}
    # ...but the pair has been written to, so the value is not the user's.
    assert await SqlSyncStore(db, "rating").known_keys("plex", "hyperfix") == {TRACK_KEY}
    assert await SqlSyncStore(db, "rating").known_keys("plex", "lastfm") == set()
