"""A dry run and a real run must count the same things.

That is the whole promise of dry-run mode: the numbers it shows are the numbers
a real run would act on. The pipeline already shares one code path, so this test
exists to catch the day something is threaded past it — a router flag, a worker
branch, or a counter that is only incremented on the write path.
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
from ratingrelay.engine.plan import WritePlan
from ratingrelay.engine.types import Intent, IntentKind, Track
from ratingrelay.schemas.relay import LastFMToConfig, PlexFromConfig

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

TRACKS = [
    Track(artist="Artist A", title="Song A", rating=9.0),
    Track(artist="Artist B", title="Song B", rating=10.0),
]


class FakeSource:
    service = "plex"

    def __init__(self, tracks: list[Track]):
        self._tracks = tracks

    async def fetch(self) -> list[Track]:
        return list(self._tracks)


class FakeTarget:
    """A target that loves whatever it is given, and records each write."""

    service = "lastfm"

    def __init__(self):
        self.writes: list[str] = []

    async def plan(self, track: Track) -> WritePlan:
        async def commit() -> None:
            self.writes.append(track.key)

        return WritePlan(
            track=track,
            target=self.service,
            intent=Intent(IntentKind.LOVE),
            target_key=f"{track.artist}||{track.title}",
            commit=commit,
        )

    async def undo(self, record) -> None:  # pragma: no cover - not exercised here
        self.writes.remove(record.key)


@pytest.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _run(db: AsyncSession, target: FakeTarget, dry_run: bool) -> dict:
    source = FakeSource(TRACKS)
    with (
        patch("ratingrelay.core.relay.adapters.source_for", AsyncMock(return_value=source)),
        patch("ratingrelay.core.relay.adapters.target_for", AsyncMock(return_value=target)),
    ):
        return await execute_relay(
            db,
            PlexFromConfig(service="plex"),
            [LastFMToConfig(service="lastfm")],
            dry_run=dry_run,
        )


async def test_dry_run_counts_match_the_real_run(db):
    target = FakeTarget()

    dry = await _run(db, target, dry_run=True)
    assert target.writes == []
    real = await _run(db, target, dry_run=False)

    assert dry["results"] == real["results"] == [
        {"service": "lastfm", "ok": 2, "err": 0, "skipped": 0}
    ]
    assert len(target.writes) == 2


async def test_dry_run_sees_the_records_the_real_run_wrote(db):
    """The second dry run has to agree with the second real run, not the first."""
    target = FakeTarget()

    await _run(db, target, dry_run=False)
    dry = await _run(db, target, dry_run=True)
    real = await _run(db, target, dry_run=False)

    expected = [{"service": "lastfm", "ok": 0, "err": 0, "skipped": 2}]
    assert dry["results"] == real["results"] == expected
    assert len(target.writes) == 2  # nothing written again


async def test_dry_run_reports_a_withdrawal_without_undoing_it(db):
    target = FakeTarget()
    await _run(db, target, dry_run=False)

    with (
        patch("ratingrelay.core.relay.adapters.source_for", AsyncMock(return_value=FakeSource([TRACKS[0]]))),
        patch("ratingrelay.core.relay.adapters.target_for", AsyncMock(return_value=target)),
    ):
        dry = await execute_relay(
            db,
            PlexFromConfig(service="plex"),
            [LastFMToConfig(service="lastfm")],
            dry_run=True,
        )

    # Reverted counts as ok, and the track whose intent did not change is skipped.
    assert dry["results"] == [{"service": "lastfm", "ok": 1, "err": 0, "skipped": 1}]
    assert len(target.writes) == 2  # the dry run undid nothing
