"""The job API as the UI uses it: stop a running job, and stream its progress.

Both used to depend on the worker living in the same process as the API. The
tests here only ever touch the HTTP surface, which is the point.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from ratingrelay.db import get_async_session
from ratingrelay.models.jobs import Job, TrackEvent
from ratingrelay.routes import jobs as jobs_module

SOURCE = json.dumps({"service": "plex", "rating_threshold": 8, "comparison": "gte"})
TARGETS = json.dumps([{"service": "lastfm"}])


@pytest.fixture
async def engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def client(factory, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from fastapi import FastAPI

    # The stream tails the database through the module's session_maker; point
    # it at the same database the request uses.
    monkeypatch.setattr(jobs_module, "session_maker", factory)

    app = FastAPI()
    app.include_router(jobs_module.router)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _make_job(factory, status: str = "running", **kwargs) -> str:
    async with factory() as db:
        job = Job(
            source_config=SOURCE,
            targets_config=TARGETS,
            status=status,
            started_at=datetime.now(timezone.utc) if status == "running" else None,
            **kwargs,
        )
        db.add(job)
        await db.commit()
        return job.id


async def _add_event(
    factory,
    job_id: str,
    artist: str,
    success: bool = True,
    reason: str | None = None,
    outcome: str | None = None,
) -> None:
    async with factory() as db:
        db.add(
            TrackEvent(
                job_id=job_id,
                artist=artist,
                title="song",
                target_service="lastfm",
                success=success,
                outcome=outcome or ("written" if success else "failed"),
                reason=reason,
            )
        )
        await db.commit()


async def test_cancel_asks_a_running_job_to_stop_instead_of_refusing(client, factory):
    job_id = await _make_job(factory, "running")

    res = await client.delete(f"/api/relay/jobs/{job_id}")

    assert res.status_code == 200
    body = res.json()
    # Still running: the worker stops at the next track boundary, not now.
    assert body["status"] == "running"
    assert body["cancel_requested"] is True


async def test_cancel_of_a_queued_job_still_cancels_it_outright(client, factory):
    job_id = await _make_job(factory, "queued")

    res = await client.delete(f"/api/relay/jobs/{job_id}")

    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"


async def test_cancel_of_a_finished_job_is_refused(client, factory):
    job_id = await _make_job(factory, "completed")

    res = await client.delete(f"/api/relay/jobs/{job_id}")

    assert res.status_code == 409


async def test_stream_replays_events_and_ends_for_a_finished_job(client, factory):
    job_id = await _make_job(factory, "completed", tracks_fetched=2, tracks_ok=2)
    await _add_event(factory, job_id, "first")
    await _add_event(factory, job_id, "second")

    res = await client.get(f"/api/relay/jobs/{job_id}/stream")

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    body = res.text
    assert body.index("event: track") < body.index('"first"') < body.index('"second"')
    assert "event: status" in body
    assert "event: done" in body
    assert '"status": "completed"' in body


async def test_stream_reports_the_row_counters_not_its_own(client, factory):
    """The worker owns the counters; the stream must not invent them."""
    job_id = await _make_job(factory, "completed", tracks_fetched=3, tracks_ok=3)

    res = await client.get(f"/api/relay/jobs/{job_id}/stream")

    status_event = [line for line in res.text.splitlines() if line.startswith("data: ")][0]
    payload = json.loads(status_event.removeprefix("data: "))
    assert payload["tracks_ok"] == 3
    assert payload["tracks_fetched"] == 3


async def test_job_detail_carries_the_typed_reason(client, factory):
    """The log labels a skip from its reason, never by matching error text."""
    job_id = await _make_job(factory, "completed")
    await _add_event(factory, job_id, "skipped", success=False, reason="already_synced")

    res = await client.get(f"/api/relay/jobs/{job_id}")

    assert res.status_code == 200
    event = res.json()["events"][0]
    assert event["reason"] == "already_synced"
    assert event["outcome"] == "failed"
    assert event["candidates"] == []


async def test_job_detail_counts_each_destination_separately(client, factory):
    """`tracks_ok` is a sum over targets; the UI needs the parts."""
    job_id = await _make_job(factory, "completed")
    await _add_event(factory, job_id, "one", success=True)
    await _add_event(factory, job_id, "two", success=True)
    await _add_event(
        factory, job_id, "three", success=False, reason="already_synced", outcome="skipped"
    )

    res = await client.get(f"/api/relay/jobs/{job_id}")

    assert res.status_code == 200
    (tally,) = res.json()["by_target"]
    assert tally == {
        "service": "lastfm",
        "written": 2,
        "failed": 0,
        "skipped": 1,
        "unmatched": 0,
    }
