"""The unmatched queue selects on the engine's reason, not on error text.

v2 matched two exact error strings. That made the queue silently empty the day
someone reworded a message, and it could not carry the candidates behind an
ambiguous match. These tests pin the reason-based query and the candidate
payload the UI needs to let a human choose.
"""

import json
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import ratingrelay.models.jobs  # noqa: F401 — register tables
import ratingrelay.models.mappings  # noqa: F401
import ratingrelay.models.services  # noqa: F401
import ratingrelay.models.sync  # noqa: F401

from fastapi import FastAPI
from ratingrelay.db import get_async_session
from ratingrelay.models.jobs import TrackEvent
from ratingrelay.routes.unmatched import router

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def client():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    app = FastAPI()
    app.include_router(router)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, factory
    await engine.dispose()


async def _event(factory, **kwargs) -> None:
    async with factory() as session:
        session.add(TrackEvent(job_id="job-1", **kwargs))
        await session.commit()


async def test_queue_returns_unmatched_reasons_with_candidates(client):
    http, factory = client
    await _event(
        factory,
        artist="Sigur Ros",
        title="Hoppipolla",
        target_service="lastfm",
        success=False,
        error="no unambiguous match for 'hoppipolla'",
        outcome="unmatched",
        reason="ambiguous_match",
        candidates=json.dumps(
            [{"artist": "Sigur Rós", "title": "Hoppípolla"},
             {"artist": "Sigur Rós", "title": "Hoppípolla (Live)"}]
        ),
    )
    await _event(
        factory,
        artist="Artist A",
        title="Song A",
        target_service="plex",
        success=False,
        error="No match found in Plex library",
        outcome="unmatched",
        reason="no_match",
    )

    response = await http.get("/api/relay/unmatched")

    assert response.status_code == 200
    rows = response.json()
    assert {(row["artist"], row["reason"]) for row in rows} == {
        ("Sigur Ros", "ambiguous_match"),
        ("Artist A", "no_match"),
    }
    ambiguous = next(row for row in rows if row["reason"] == "ambiguous_match")
    assert [candidate["title"] for candidate in ambiguous["candidates"]] == [
        "Hoppípolla",
        "Hoppípolla (Live)",
    ]


async def test_queue_ignores_a_failed_write(client):
    """A write that failed is not a matching problem and does not belong here."""
    http, factory = client
    await _event(
        factory,
        artist="Artist A",
        title="Song A",
        target_service="lastfm",
        success=False,
        error="Service unavailable",
        outcome="failed",
        reason="write_failed",
    )

    response = await http.get("/api/relay/unmatched")

    assert response.status_code == 200
    assert response.json() == []


async def test_queue_is_not_pinned_to_the_old_error_strings(client):
    """A reworded message with the right reason still shows up."""
    http, factory = client
    await _event(
        factory,
        artist="Artist B",
        title="Song B",
        target_service="listenbrainz",
        success=False,
        error="could not resolve a recording MBID",  # new wording
        outcome="unmatched",
        reason="no_recording_mbid",
    )

    response = await http.get("/api/relay/unmatched")

    assert [row["artist"] for row in response.json()] == ["Artist B"]


async def test_queue_counts_repeats_and_keeps_the_newest_first(client):
    http, factory = client
    for _ in range(3):
        await _event(
            factory,
            artist="Artist A",
            title="Song A",
            target_service="lastfm",
            success=False,
            error="No match found in Plex library",
            outcome="unmatched",
            reason="no_match",
        )

    response = await http.get("/api/relay/unmatched")

    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["count"] == 3


async def test_queue_drops_a_track_that_has_synced_since_it_failed(client):
    """History is not a problem list.

    The live account carried 225 rows for weeks, 192 of which resolve now: every
    failed run had appended to the queue and no successful run ever removed
    anything. A row belongs here only while the newest thing we know about the
    track is that it failed.
    """
    http, factory = client
    await _event(
        factory,
        artist="The Smiths",
        title="Bigmouth Strikes Again",
        target_service="listenbrainz",
        success=False,
        error="No MusicBrainz recording ID available",
        outcome="unmatched",
        reason="no_recording_mbid",
        created_at=datetime(2026, 5, 11, 16, 23),
    )
    await _event(
        factory,
        artist="The Smiths",
        title="Bigmouth Strikes Again",
        target_service="listenbrainz",
        success=True,
        outcome="written",
        created_at=datetime(2026, 11, 13, 1, 37),
    )

    assert (await http.get("/api/relay/unmatched")).json() == []


async def test_queue_takes_the_track_back_when_it_fails_again(client):
    """A track that regresses comes back, and only its new failures count."""
    http, factory = client
    for minute in range(3):
        await _event(
            factory,
            artist="Artist A",
            title="Song A",
            target_service="lastfm",
            success=False,
            error="No match found in Plex library",
            outcome="unmatched",
            reason="no_match",
            created_at=datetime(2026, 5, 1, 12, minute),
        )
    await _event(
        factory,
        artist="Artist A",
        title="Song A",
        target_service="lastfm",
        success=True,
        outcome="written",
        created_at=datetime(2026, 6, 1, 12, 0),
    )
    for minute in range(2):
        await _event(
            factory,
            artist="Artist A",
            title="Song A",
            target_service="lastfm",
            success=False,
            error="No match found in Plex library",
            outcome="unmatched",
            reason="no_match",
            created_at=datetime(2026, 7, 1, 12, minute),
        )

    rows = (await http.get("/api/relay/unmatched")).json()

    assert len(rows) == 1
    assert rows[0]["count"] == 2
    assert rows[0]["last_failed"] == "2026-07-01T12:01:00"


# ── unrevertable writes ───────────────────────────────────────────────────────


async def _record(factory, **kwargs) -> None:
    from ratingrelay.models.sync import SyncRecord

    async with factory() as session:
        session.add(SyncRecord(**kwargs))
        await session.commit()


def _record_args(**over) -> dict:
    row = {
        "source_service": "plex",
        "target_service": "lastfm",
        "normalized_key": "the smiths||bigmouth strikes again",
        "recording_mbid": "mbid-1",
        "intent": "love",
        "rating": None,
        "target_key": json.dumps(["The Smiths", "Bigmouth Strikes Again"]),
    }
    row.update(over)
    return row


async def test_a_record_with_its_target_key_is_not_reported(client):
    http, factory = client
    await _record(factory, **_record_args())

    response = await http.get("/api/relay/unrevertable")

    assert response.status_code == 200
    assert response.json() == []


async def test_a_record_without_its_target_key_is_reported(client):
    """The v2 rows: we know we loved something on Last.fm, not which name."""
    http, factory = client
    await _record(factory, **_record_args(target_key=None))

    response = await http.get("/api/relay/unrevertable")

    assert response.json() == [
        {
            "artist": "the smiths",
            "title": "bigmouth strikes again",
            "target_service": "lastfm",
            "intent": "love",
            "reason": "unrecorded_target",
            "error": "no recorded Last.fm track name",
        }
    ]


async def test_a_listenbrainz_record_is_undoable_from_its_mbid(client):
    """Feedback is addressed by recording MBID, so a missing target_key is fine."""
    http, factory = client
    await _record(
        factory,
        **_record_args(target_service="listenbrainz", target_key=None),
    )

    assert (await http.get("/api/relay/unrevertable")).json() == []


async def test_a_listenbrainz_record_without_an_mbid_is_reported(client):
    http, factory = client
    await _record(
        factory,
        **_record_args(
            target_service="listenbrainz", target_key=None, recording_mbid=None
        ),
    )

    body = (await http.get("/api/relay/unrevertable")).json()

    assert [r["error"] for r in body] == ["no recorded recording MBID"]


async def test_rows_for_a_service_we_no_longer_write_to_are_left_out(client):
    http, factory = client
    await _record(factory, **_record_args(target_service="spotify", target_key=None))

    assert (await http.get("/api/relay/unrevertable")).json() == []
