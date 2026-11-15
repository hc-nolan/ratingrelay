"""Reset takes two forms, and they must not be confused for one another.

``reset`` (the default) undoes exactly what this tool recorded writing to one
target and forgets it. ``reset(all=True)`` wipes the whole service. These tests
pin the difference: the narrow reset must never touch a record it cannot undo,
and must never claim to have undone one.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import ratingrelay.models.jobs  # noqa: F401 — register tables
import ratingrelay.models.mappings  # noqa: F401
import ratingrelay.models.services  # noqa: F401
import ratingrelay.models.sync  # noqa: F401

from ratingrelay.core.relay import execute_relay
from ratingrelay.db.store import SqlSyncStore
from ratingrelay.engine.normalize import track_key
from ratingrelay.engine.store import SyncedTrack
from ratingrelay.schemas.relay import ResetFromConfig

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


class FakeTarget:
    """Records what reset asked it to undo, and can refuse."""

    service = "lastfm"

    def __init__(self, refuse: tuple[str, ...] = ()):
        self.undone: list[str] = []
        self._refuse = set(refuse)

    def undoable(self, record: SyncedTrack) -> None:
        if record.key in self._refuse:
            raise ValueError("no recorded Last.fm track name")

    async def undo(self, record: SyncedTrack) -> None:
        self.undoable(record)
        self.undone.append(record.key)


@pytest.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db: AsyncSession, service: str = "lastfm", *artists: str) -> None:
    store = SqlSyncStore(db)
    for artist in artists:
        await store.remember(
            "plex",
            service,
            SyncedTrack(
                key=track_key(artist, "Song"),
                intent="love",
                recording_mbid="mb-1",
                target_key=f"{artist}||Song",
            ),
        )


def _capture():
    events: list[tuple[str, bool, bool]] = []

    async def on_track(track, target, success, error, skipped=False, outcome=None):
        events.append((f"{track['artist']} - {track['title']}", success, skipped))

    return events, on_track


async def test_reset_undoes_only_the_writes_it_recorded(db):
    await _seed(db, "lastfm", "Artist A", "Artist B")
    # A record for another target must be irrelevant to this reset.
    await _seed(db, "listenbrainz", "Artist C")

    target = FakeTarget()
    events, on_track = _capture()
    with patch("ratingrelay.core.relay.adapters.target_for", AsyncMock(return_value=target)):
        result = await execute_relay(
            db, ResetFromConfig(target_service="lastfm"), [], on_track=on_track
        )

    assert target.undone == [track_key("Artist A", "Song"), track_key("Artist B", "Song")]
    assert result["results"] == [{"service": "lastfm", "ok": 2, "err": 0, "skipped": 0}]
    assert all(success for _, success, _ in events)
    remaining = await SqlSyncStore(db).records_for_target("lastfm")
    assert remaining == []
    assert len(await SqlSyncStore(db).records_for_target("listenbrainz")) == 1


async def test_reset_dry_run_reports_without_undoing(db):
    await _seed(db, "lastfm", "Artist A", "Artist B")

    target = FakeTarget()
    events, on_track = _capture()
    with patch("ratingrelay.core.relay.adapters.target_for", AsyncMock(return_value=target)):
        result = await execute_relay(
            db,
            ResetFromConfig(target_service="lastfm"),
            [],
            on_track=on_track,
            dry_run=True,
        )

    assert target.undone == []
    assert result["results"] == [{"service": "lastfm", "ok": 2, "err": 0, "skipped": 0}]
    # Skipped in the UI sense: nothing was written, but the run succeeded.
    assert [skipped for _, _, skipped in events] == [True, True]
    assert len(await SqlSyncStore(db).records_for_target("lastfm")) == 2


async def test_reset_keeps_a_record_it_cannot_undo(db):
    """A record that does not say what we wrote is left alone, not guessed at."""
    await _seed(db, "lastfm", "Artist A", "Artist B")

    target = FakeTarget(refuse=(track_key("Artist B", "Song"),))
    events, on_track = _capture()
    with patch("ratingrelay.core.relay.adapters.target_for", AsyncMock(return_value=target)):
        result = await execute_relay(
            db, ResetFromConfig(target_service="lastfm"), [], on_track=on_track
        )

    assert target.undone == [track_key("Artist A", "Song")]
    assert result["results"] == [{"service": "lastfm", "ok": 1, "err": 1, "skipped": 0}]
    remaining = await SqlSyncStore(db).records_for_target("lastfm")
    assert [record.key for _, record in remaining] == [track_key("Artist B", "Song")]


async def test_reset_dry_run_refuses_the_same_records_a_real_run_refuses(db):
    """The dry run is the authority: it must not promise an undo it cannot make."""
    await _seed(db, "lastfm", "Artist A", "Artist B")

    target = FakeTarget(refuse=(track_key("Artist B", "Song"),))
    events, on_track = _capture()
    with patch("ratingrelay.core.relay.adapters.target_for", AsyncMock(return_value=target)):
        result = await execute_relay(
            db,
            ResetFromConfig(target_service="lastfm"),
            [],
            on_track=on_track,
            dry_run=True,
        )

    assert target.undone == []
    assert result["results"] == [{"service": "lastfm", "ok": 1, "err": 1, "skipped": 0}]
    assert len(await SqlSyncStore(db).records_for_target("lastfm")) == 2


async def test_reset_reports_an_unconfigured_target_without_undoing(db):
    await _seed(db, "lastfm", "Artist A")

    events, on_track = _capture()
    with patch(
        "ratingrelay.core.relay.adapters.target_for",
        AsyncMock(side_effect=ValueError("Last.fm not configured")),
    ):
        result = await execute_relay(
            db, ResetFromConfig(target_service="lastfm"), [], on_track=on_track
        )

    assert result["results"] == [{"service": "lastfm", "ok": 0, "err": 1, "skipped": 0}]
    # The event's label comes from the stored key, which is normalized.
    assert events == [("artist a - song", False, False)]
    assert len(await SqlSyncStore(db).records_for_target("lastfm")) == 1


async def test_reset_all_wipes_the_service_and_its_records(db):
    await _seed(db, "lastfm", "Artist A", "Artist B")
    await _seed(db, "listenbrainz", "Artist C")

    with patch(
        "ratingrelay.core.relay.reset_lastfm", AsyncMock(return_value=(2, 0))
    ) as wipe:
        result = await execute_relay(
            db, ResetFromConfig(target_service="lastfm", all=True), []
        )

    wipe.assert_awaited_once()
    assert result["results"] == [{"service": "lastfm", "ok": 2, "err": 0, "skipped": 0}]
    assert await SqlSyncStore(db).records_for_target("lastfm") == []
    # Only the wiped service loses its history.
    assert len(await SqlSyncStore(db).records_for_target("listenbrainz")) == 1


async def test_reset_all_is_not_the_default(db):
    """The destructive form has to be asked for."""
    assert ResetFromConfig().all is False
