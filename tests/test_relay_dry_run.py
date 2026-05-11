"""
Tests for dry-run write suppression in execute_relay / write_to_* functions.

Uses a real in-memory SQLite DB and fake credential/service layers to verify
that dry_run=True suppresses external writes and SyncRecord creation while
still calling on_track for every track (including skipped).
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select
from sqlmodel import SQLModel
from unittest.mock import AsyncMock, patch, MagicMock

import ratingrelay.models.jobs  # noqa: F401
import ratingrelay.models.sync  # noqa: F401
import ratingrelay.models.mappings  # noqa: F401
import ratingrelay.models.services  # noqa: F401

from ratingrelay.core.relay import write_to_lastfm, write_to_listenbrainz, _record_sync
from ratingrelay.models.sync import SyncRecord
from ratingrelay.schemas.relay import LastFMToConfig, ListenBrainzToConfig

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

TRACK_A = {"artist": "Artist A", "title": "Song A"}
TRACK_B = {"artist": "Artist B", "title": "Song B"}
LFM_CONFIG = LastFMToConfig(service="lastfm")
LBZ_CONFIG = ListenBrainzToConfig(service="listenbrainz", feedback_type="love")


@pytest.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _sync_record_count(db: AsyncSession) -> int:
    result = await db.execute(select(SyncRecord))
    return len(result.scalars().all())


# ---------------------------------------------------------------------------
# write_to_lastfm dry_run=True
# ---------------------------------------------------------------------------


async def test_lastfm_dry_run_suppresses_write(db):
    """dry_run=True: on_track called with success=True, no SyncRecord written."""
    calls = []

    async def capture(track, target, success, error, skipped=False):
        calls.append((track, target, success, error, skipped))

    fake_network = MagicMock()

    with patch("ratingrelay.core.relay._resolve_lastfm_network", AsyncMock(return_value=fake_network)):
        ok, err, skipped = await write_to_lastfm(
            db, [TRACK_A], LFM_CONFIG, "plex", capture, dry_run=True
        )

    assert ok == 1
    assert err == 0
    assert skipped == 0
    assert calls == [(TRACK_A, "lastfm", True, None, False)]
    assert await _sync_record_count(db) == 0
    # External .love() was never called
    fake_network.get_track.assert_not_called()


async def test_lastfm_dry_run_skipped_track_fires_on_track(db):
    """Already-synced tracks: on_track called with skipped=True, no new SyncRecord."""
    # Pre-populate sync history
    await _record_sync(db, "plex", "lastfm", TRACK_A)
    before = await _sync_record_count(db)

    calls = []

    async def capture(track, target, success, error, skipped=False):
        calls.append((track, target, success, error, skipped))

    fake_network = MagicMock()

    with patch("ratingrelay.core.relay._resolve_lastfm_network", AsyncMock(return_value=fake_network)):
        ok, err, skip_count = await write_to_lastfm(
            db, [TRACK_A], LFM_CONFIG, "plex", capture, dry_run=True
        )

    assert ok == 0
    assert skip_count == 1
    assert calls == [(TRACK_A, "lastfm", False, None, True)]
    # No new SyncRecord was written
    assert await _sync_record_count(db) == before


async def test_lastfm_real_run_after_dry_run_writes_same_tracks(db):
    """Dry-run does not consume sync state; a real run afterwards writes the same tracks."""
    calls_dry: list = []
    calls_real: list = []

    async def capture_dry(track, target, success, error, skipped=False):
        calls_dry.append((success, skipped))

    async def capture_real(track, target, success, error, skipped=False):
        calls_real.append((success, skipped))

    fake_network = MagicMock()

    with patch("ratingrelay.core.relay._resolve_lastfm_network", AsyncMock(return_value=fake_network)):
        # Dry run — should NOT create SyncRecord
        await write_to_lastfm(db, [TRACK_A], LFM_CONFIG, "plex", capture_dry, dry_run=True)
        assert await _sync_record_count(db) == 0

        # Real run — should create SyncRecord
        with patch("ratingrelay.core.relay.run_sync", new=AsyncMock(return_value=None)):
            await write_to_lastfm(db, [TRACK_A], LFM_CONFIG, "plex", capture_real, dry_run=False)

    assert any(s is True for s, _ in calls_real)
    assert await _sync_record_count(db) == 1


async def test_lastfm_dry_run_unmatched_is_error(db):
    """Missing artist/title appears as error even in dry-run (faithful simulation)."""
    calls = []

    async def capture(track, target, success, error, skipped=False):
        calls.append((success, error, skipped))

    bad_track = {"artist": "", "title": ""}
    fake_network = MagicMock()

    with patch("ratingrelay.core.relay._resolve_lastfm_network", AsyncMock(return_value=fake_network)):
        ok, err, _ = await write_to_lastfm(
            db, [bad_track], LFM_CONFIG, "plex", capture, dry_run=True
        )

    assert ok == 0
    assert err == 1
    assert calls[0] == (False, "Missing artist or title", False)


# ---------------------------------------------------------------------------
# write_to_listenbrainz dry_run=True (no MBID lookup needed for already-known MBID)
# ---------------------------------------------------------------------------


async def test_listenbrainz_dry_run_suppresses_write(db):
    """dry_run=True with known MBID: on_track called, no SyncRecord, no submit."""
    calls = []

    async def capture(track, target, success, error, skipped=False):
        calls.append((track, target, success, error, skipped))

    track_with_mbid = {**TRACK_A, "recording_mbid": "aaaaaaaa-0000-0000-0000-000000000001"}
    fake_client = MagicMock()

    with (
        patch("ratingrelay.core.relay.get_credential", AsyncMock(return_value=MagicMock(token="tok"))),
        patch("ratingrelay.core.relay.settings") as mock_settings,
        patch("ratingrelay.core.relay.ListenBrainz", return_value=fake_client),
    ):
        mock_settings.listenbrainz_token = "tok"
        ok, err, _ = await write_to_listenbrainz(
            db, [track_with_mbid], LBZ_CONFIG, "plex", capture, dry_run=True
        )

    assert ok == 1
    assert err == 0
    assert calls[0][2] is True
    assert calls[0][4] is False  # not skipped
    assert await _sync_record_count(db) == 0
    fake_client.submit_user_feedback.assert_not_called()


async def test_listenbrainz_dry_run_skipped_fires_on_track(db):
    """Already-synced LBZ track in dry-run: on_track called with skipped=True."""
    track_with_mbid = {**TRACK_A, "recording_mbid": "aaaaaaaa-0000-0000-0000-000000000001"}
    await _record_sync(db, "plex", "listenbrainz", TRACK_A)

    calls = []

    async def capture(track, target, success, error, skipped=False):
        calls.append((success, skipped))

    fake_client = MagicMock()

    with (
        patch("ratingrelay.core.relay.get_credential", AsyncMock(return_value=MagicMock(token="tok"))),
        patch("ratingrelay.core.relay.settings") as mock_settings,
        patch("ratingrelay.core.relay.ListenBrainz", return_value=fake_client),
    ):
        mock_settings.listenbrainz_token = "tok"
        ok, err, skip_count = await write_to_listenbrainz(
            db, [track_with_mbid], LBZ_CONFIG, "plex", capture, dry_run=True
        )

    assert skip_count == 1
    assert calls == [(False, True)]
