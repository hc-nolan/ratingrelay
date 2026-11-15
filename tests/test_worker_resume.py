"""A job whose worker died resumes; it does not repeat itself.

The difference matters because the second attempt would otherwise count the whole
library again and journal every track twice, and the job page would show a run
twice as long as the run was.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from ratingrelay.core import worker
from ratingrelay.engine.types import OutcomeStatus, Reason, Track, TrackOutcome
from ratingrelay.models.jobs import Job, TrackEvent

SOURCE = json.dumps({"service": "plex", "rating_threshold": 8, "comparison": "gte"})
TARGETS = json.dumps([{"service": "lastfm"}])


@pytest.fixture
async def engine(tmp_path):
    """A file-backed database: the worker needs two independent sessions."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _outcome(artist: str, status: OutcomeStatus, reason: Reason | None) -> TrackOutcome:
    return TrackOutcome(
        track=Track(artist=artist, title="song"),
        target="lastfm",
        status=status,
        reason=reason,
    )


def _converged(artist: str) -> TrackOutcome:
    """What the pipeline reports for a track whose write already happened."""
    return _outcome(artist, OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED)


def _written(artist: str) -> TrackOutcome:
    return _outcome(artist, OutcomeStatus.WRITTEN, None)


async def _interrupted_job(factory) -> str:
    """A job that got three tracks in before its worker was killed."""
    async with factory() as db:
        job = Job(
            source_config=SOURCE,
            targets_config=TARGETS,
            status="queued",
            started_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            tracks_fetched=3,
            tracks_ok=3,
            tracks_skipped=7,
        )
        db.add(job)
        await db.commit()
        db.add(
            TrackEvent(
                job_id=job.id,
                artist="done before the crash",
                title="song",
                target_service="lastfm",
                success=True,
            )
        )
        await db.commit()
        return job.id


async def _events(factory, job_id: str) -> list[TrackEvent]:
    async with factory() as db:
        result = await db.execute(select(TrackEvent).where(TrackEvent.job_id == job_id))
        return list(result.scalars().all())


async def test_a_restarted_job_resumes_its_counters_and_its_journal(monkeypatch, factory):
    job_id = await _interrupted_job(factory)

    async def fake_execute(db, source, targets, on_fetch_complete, on_track, dry_run):
        await on_fetch_complete(10)
        # Nine of the ten were converged by the attempt that died; only the tenth
        # is left. The echo of the nine must not be recorded again.
        for n in range(1, 10):
            await on_track(
                {"artist": f"track {n}", "title": "song"},
                "lastfm",
                True,
                None,
                True,
                _converged(f"track {n}"),
            )
        await on_track(
            {"artist": "track 10", "title": "song"},
            "lastfm",
            True,
            None,
            False,
            _written("track 10"),
        )
        return {"results": []}

    monkeypatch.setattr(worker, "session_maker", factory)
    monkeypatch.setattr(worker, "execute_relay", fake_execute)

    await worker.run_job(job_id)

    async with factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        assert job.status == "completed"
        # Three writes before the crash, one after. Seven skips before, none
        # recounted: the resumed attempt only reports what it did.
        assert (job.tracks_ok, job.tracks_err, job.tracks_skipped) == (4, 0, 7)
        # It is one run, so it keeps the first attempt's start time. SQLite hands
        # datetimes back naive, so the comparison drops the zone rather than
        # pretending the column kept it.
        assert job.started_at == datetime(2026, 1, 1, 12, 0)

    events = await _events(factory, job_id)
    assert [event.artist for event in events] == ["done before the crash", "track 10"]


async def test_a_first_attempt_still_journals_what_was_already_in_sync(monkeypatch, factory):
    """Suppression is for a resumed attempt only.

    On a fresh run, "already synced" is the answer to "what did this run do with
    the library", and the job page has to be able to say 352 of them.
    """
    async with factory() as db:
        job = Job(source_config=SOURCE, targets_config=TARGETS)
        db.add(job)
        await db.commit()
        job_id = job.id

    async def fake_execute(db, source, targets, on_fetch_complete, on_track, dry_run):
        await on_fetch_complete(2)
        await on_track(
            {"artist": "in sync", "title": "song"},
            "lastfm",
            True,
            None,
            True,
            _converged("in sync"),
        )
        await on_track(
            {"artist": "new", "title": "song"},
            "lastfm",
            True,
            None,
            False,
            _written("new"),
        )
        return {"results": []}

    monkeypatch.setattr(worker, "session_maker", factory)
    monkeypatch.setattr(worker, "execute_relay", fake_execute)

    await worker.run_job(job_id)

    async with factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        assert (job.tracks_ok, job.tracks_skipped) == (1, 1)

    assert len(await _events(factory, job_id)) == 2


async def test_a_resumed_job_can_still_be_cancelled(monkeypatch, factory):
    """Cancelling a job that is already resuming has to work too."""
    job_id = await _interrupted_job(factory)

    async def fake_execute(db, source, targets, on_fetch_complete, on_track, dry_run):
        await on_fetch_complete(2)
        await on_track(
            {"artist": "in sync", "title": "song"},
            "lastfm",
            True,
            None,
            True,
            _converged("in sync"),
        )
        async with factory() as other:
            row = await other.get(Job, job_id)
            row.cancel_requested = True
            other.add(row)
            await other.commit()
        # The suppressed outcome is where the flag is observed for the first
        # time, so stopping must not depend on there being a new write to record.
        await on_track(
            {"artist": "in sync too", "title": "song"},
            "lastfm",
            True,
            None,
            True,
            _converged("in sync too"),
        )

    monkeypatch.setattr(worker, "session_maker", factory)
    monkeypatch.setattr(worker, "execute_relay", fake_execute)

    await worker.run_job(job_id)

    async with factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        assert job.status == "cancelled"
        assert (job.tracks_ok, job.tracks_skipped) == (3, 7)
