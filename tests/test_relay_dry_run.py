"""
Tests for dry-run write suppression through ``execute_relay``.

Uses a real in-memory SQLite DB together with fake service layers, and verifies
that dry_run=True suppresses external writes and SyncRecord creation while still
calling on_track for every track (including skipped).

The service fakes are injected where the adapters build clients, so these tests
exercise the wrapper plus the real engine pipeline rather than a mock of it.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select
from sqlmodel import SQLModel
from unittest.mock import AsyncMock, patch

import ratingrelay.models.jobs  # noqa: F401
import ratingrelay.models.sync  # noqa: F401
import ratingrelay.models.mappings  # noqa: F401
import ratingrelay.models.services  # noqa: F401

from ratingrelay.core.relay import execute_relay
from ratingrelay.db.store import SqlSyncStore
from ratingrelay.engine.credentials import (
    LastFmCredentials,
    ListenBrainzCredentials,
)
from ratingrelay.engine.normalize import track_key
from ratingrelay.engine.store import SyncedTrack
from ratingrelay.engine.types import Track
from ratingrelay.models.sync import SyncRecord
from ratingrelay.schemas.relay import LastFMToConfig, ListenBrainzToConfig, PlexFromConfig

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

TRACK_A = {"artist": "Artist A", "title": "Song A"}
TRACK_B = {"artist": "Artist B", "title": "Song B"}
LFM_CONFIG = LastFMToConfig(service="lastfm")
LBZ_CONFIG = ListenBrainzToConfig(service="listenbrainz")

LFM_CREDS = LastFmCredentials(
    api_key="key", api_secret="secret", username="user", password_hash="hash"
)
LBZ_CREDS = ListenBrainzCredentials(token="tok", username="user")


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


class _ListSource:
    """A Plex source that hands back a fixed list, so only the target is real."""

    service = "plex"

    def __init__(self, tracks: list[dict]):
        self._tracks = tracks

    async def fetch(self) -> list[Track]:
        return [
            Track(
                artist=t["artist"],
                title=t["title"],
                recording_mbid=t.get("recording_mbid"),
                rating=10.0,
            )
            for t in self._tracks
        ]


async def _relay(db, tracks, config, on_track, dry_run) -> tuple[int, int, int]:
    """One relay from Plex to *config*, returning that target's (ok, err, skipped)."""
    with patch(
        "ratingrelay.core.relay.adapters.source_for",
        AsyncMock(return_value=_ListSource(tracks)),
    ):
        result = await execute_relay(
            db, PlexFromConfig(service="plex"), [config], on_track=on_track, dry_run=dry_run
        )
    row = result["results"][0]
    return row["ok"], row["err"], row["skipped"]


def _lastfm_patches():
    """Credential stub plus a fake pylast module, for the lifetime of one write."""
    return (
        patch(
            "ratingrelay.core.factory.lastfm_credentials",
            AsyncMock(return_value=LFM_CREDS),
        ),
        patch("ratingrelay.engine.services.lastfm.pylast"),
    )


def _listenbrainz_patches():
    return (
        patch(
            "ratingrelay.core.factory.listenbrainz_credentials",
            AsyncMock(return_value=LBZ_CREDS),
        ),
        patch("ratingrelay.engine.services.listenbrainz.ListenBrainz"),
    )


# ---------------------------------------------------------------------------
# Last.fm, dry run
# ---------------------------------------------------------------------------


async def test_lastfm_dry_run_suppresses_write(db):
    """dry_run=True: on_track called with success=True, no SyncRecord written."""
    calls = []

    async def capture(track, target, success, error, skipped=False, outcome=None):
        calls.append(({"artist": track["artist"], "title": track["title"]}, target, success, error, skipped))

    creds_patch, pylast_patch = _lastfm_patches()
    with creds_patch, pylast_patch as fake_pylast:
        ok, err, skipped = await _relay(db, [TRACK_A], LFM_CONFIG, capture, dry_run=True)

    assert ok == 1
    assert err == 0
    assert skipped == 0
    assert calls == [(TRACK_A, "lastfm", True, None, False)]
    assert await _sync_record_count(db) == 0
    # External .love() was never called
    fake_pylast.Track.assert_not_called()


async def test_lastfm_dry_run_skipped_track_fires_on_track(db):
    """Already-synced tracks: on_track called with skipped=True, no new SyncRecord."""
    # Pre-populate sync history, with the intent this track now implies
    await SqlSyncStore(db, "love").remember(
        "plex", "lastfm", SyncedTrack(track_key("Artist A", "Song A"), intent="love")
    )
    before = await _sync_record_count(db)

    calls = []

    async def capture(track, target, success, error, skipped=False, outcome=None):
        calls.append(({"artist": track["artist"], "title": track["title"]}, target, success, error, skipped))

    creds_patch, pylast_patch = _lastfm_patches()
    with creds_patch, pylast_patch:
        ok, err, skip_count = await _relay(db, [TRACK_A], LFM_CONFIG, capture, dry_run=True)

    assert ok == 0
    assert err == 0
    assert skip_count == 1
    assert calls == [(TRACK_A, "lastfm", False, None, True)]
    # No new SyncRecord was written
    assert await _sync_record_count(db) == before


async def test_lastfm_real_run_after_dry_run_writes_same_tracks(db):
    """Dry-run does not consume sync state; a real run afterwards writes the same tracks."""
    calls_dry: list = []
    calls_real: list = []

    async def capture_dry(track, target, success, error, skipped=False, outcome=None):
        calls_dry.append((success, skipped))

    async def capture_real(track, target, success, error, skipped=False, outcome=None):
        calls_real.append((success, skipped))

    creds_patch, pylast_patch = _lastfm_patches()
    with creds_patch, pylast_patch:
        # Dry run — should NOT create SyncRecord
        await _relay(db, [TRACK_A], LFM_CONFIG, capture_dry, dry_run=True)
        assert await _sync_record_count(db) == 0

        # Real run — should create SyncRecord
        await _relay(db, [TRACK_A], LFM_CONFIG, capture_real, dry_run=False)

    assert any(s is True for s, _ in calls_real)
    assert await _sync_record_count(db) == 1


async def test_lastfm_dry_run_unmatched_is_error(db):
    """Missing artist/title appears as error even in dry-run (faithful simulation)."""
    calls = []

    async def capture(track, target, success, error, skipped=False, outcome=None):
        calls.append((success, error, skipped))

    bad_track = {"artist": "", "title": ""}
    creds_patch, pylast_patch = _lastfm_patches()
    with creds_patch, pylast_patch:
        ok, err, _ = await _relay(db, [bad_track], LFM_CONFIG, capture, dry_run=True)

    assert ok == 0
    assert err == 1
    assert calls[0] == (False, "Missing artist or title", False)


# ---------------------------------------------------------------------------
# ListenBrainz, dry run (no MBID lookup needed for an already-known MBID)
# ---------------------------------------------------------------------------


async def test_listenbrainz_dry_run_suppresses_write(db):
    """dry_run=True with known MBID: on_track called, no SyncRecord, no submit."""
    calls = []

    async def capture(track, target, success, error, skipped=False, outcome=None):
        calls.append(({"artist": track["artist"], "title": track["title"]}, target, success, error, skipped))

    track_with_mbid = {**TRACK_A, "recording_mbid": "aaaaaaaa-0000-0000-0000-000000000001"}
    creds_patch, lbz_patch = _listenbrainz_patches()
    with creds_patch, lbz_patch as fake_listenbrainz:
        fake_client = fake_listenbrainz.return_value
        ok, err, _ = await _relay(db, [track_with_mbid], LBZ_CONFIG, capture, dry_run=True)

    assert ok == 1
    assert err == 0
    assert calls[0][2] is True
    assert calls[0][4] is False  # not skipped
    assert await _sync_record_count(db) == 0
    fake_client.submit_user_feedback.assert_not_called()


async def test_listenbrainz_dry_run_skipped_fires_on_track(db):
    """Already-synced LBZ track in dry-run: on_track called with skipped=True."""
    track_with_mbid = {**TRACK_A, "recording_mbid": "aaaaaaaa-0000-0000-0000-000000000001"}
    await SqlSyncStore(db, "love").remember(
        "plex", "listenbrainz", SyncedTrack(track_key("Artist A", "Song A"), intent="love")
    )

    calls = []

    async def capture(track, target, success, error, skipped=False, outcome=None):
        calls.append((success, skipped))

    creds_patch, lbz_patch = _listenbrainz_patches()
    with creds_patch, lbz_patch:
        ok, err, skip_count = await _relay(db, [track_with_mbid], LBZ_CONFIG, capture, dry_run=True)

    assert ok == 0
    assert err == 0
    assert skip_count == 1
    assert calls == [(False, True)]
