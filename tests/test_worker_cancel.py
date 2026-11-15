"""Cancellation and the database-tailed job stream.

These are the two things a run could not do before: be stopped once it had
started, and report progress to anyone who was not in the same process.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from ratingrelay.core import worker
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


async def _queue_job(factory) -> str:
    async with factory() as db:
        job = Job(source_config=SOURCE, targets_config=TARGETS)
        db.add(job)
        await db.commit()
        return job.id


async def _get_job(factory, job_id: str) -> Job:
    async with factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        return job


async def test_a_cancel_request_stops_the_run_at_a_track_boundary(monkeypatch, factory):
    job_id = await _queue_job(factory)
    seen: list[int] = []

    async def fake_execute(db, source, targets, on_fetch_complete, on_track, dry_run):
        await on_fetch_complete(5)
        for n in range(1, 6):
            seen.append(n)
            await on_track(
                {"artist": f"artist {n}", "title": "song"},
                "lastfm",
                True,
                None,
                False,
                None,
            )
            if n == 2:
                # What DELETE /api/relay/jobs/{id} does while the run is going.
                async with factory() as other:
                    row = await other.get(Job, job_id)
                    row.cancel_requested = True
                    other.add(row)
                    await other.commit()
        return {"results": []}

    monkeypatch.setattr(worker, "session_maker", factory)
    monkeypatch.setattr(worker, "execute_relay", fake_execute)

    await worker.run_job(job_id)

    job = await _get_job(factory, job_id)
    # The flag is set after track 2 is recorded, so track 3 finishes (its
    # boundary is the first one that can observe it) and 4 and 5 never start.
    assert seen == [1, 2, 3]
    assert job.status == "cancelled"
    assert job.tracks_ok == 3
    assert job.completed_at is not None

    async with factory() as db:
        events = (
            await db.execute(select(TrackEvent).where(TrackEvent.job_id == job_id))
        ).scalars().all()
    assert len(events) == 3, "the tracks that ran kept their events"


async def test_a_cancel_requested_before_the_fetch_stops_without_writing(monkeypatch, factory):
    job_id = await _queue_job(factory)
    async with factory() as db:
        job = await db.get(Job, job_id)
        job.cancel_requested = True
        db.add(job)
        await db.commit()

    written: list[int] = []

    async def fake_execute(db, source, targets, on_fetch_complete, on_track, dry_run):
        await on_fetch_complete(3)
        written.append(1)  # unreachable
        return {"results": []}

    monkeypatch.setattr(worker, "session_maker", factory)
    monkeypatch.setattr(worker, "execute_relay", fake_execute)

    await worker.run_job(job_id)

    job = await _get_job(factory, job_id)
    assert written == []
    assert job.status == "cancelled"
    assert job.tracks_fetched == 3


async def test_a_stale_running_job_with_a_cancel_request_does_not_resume(monkeypatch, factory):
    """A cancel that arrived while the process was dying still counts."""
    job_id = await _queue_job(factory)
    async with factory() as db:
        job = await db.get(Job, job_id)
        job.status = "running"
        job.cancel_requested = True
        db.add(job)
        await db.commit()

    monkeypatch.setattr(worker, "session_maker", factory)

    # worker_loop is an infinite loop; run one iteration's stale-reset by
    # calling it with a sleep that raises after the reset pass.
    class _Stop(Exception):
        pass

    async def _stop(*_args, **_kwargs):
        raise _Stop

    monkeypatch.setattr(worker.asyncio, "sleep", _stop)
    with pytest.raises(_Stop):
        await worker.worker_loop()

    job = await _get_job(factory, job_id)
    assert job.status == "cancelled"
    assert job.completed_at is not None
