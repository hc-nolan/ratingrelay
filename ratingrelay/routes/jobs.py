"""
Job queue API routes.

POST   /api/relay/jobs           — queue a new job
GET    /api/relay/jobs           — list all jobs, newest first
DELETE /api/relay/jobs/{id}      — cancel a queued, scheduled or running job
GET    /api/relay/jobs/{id}/stream — SSE stream of job events

Progress is not pushed from the worker: every event is a ``track_event`` row, so
the stream tails the database. That is what makes a two-process deployment (or a
restart mid-run) work, and it means a reconnecting browser replays exactly what
it missed.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.db import get_async_session, session_maker
from ratingrelay.models.jobs import Job, TrackEvent
from ratingrelay.schemas.relay import FromConfig, ToConfig, source_adapter as _source_adapter, targets_adapter as _targets_adapter
from ratingrelay.settings import logger

router = APIRouter(prefix="/api/relay", tags=["jobs"])

_TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled"}
_ACTIVE_STATUSES = {"queued", "running", "scheduled"}
# How often the stream looks for new rows. A run writes about one track a
# second, so a second of lag is below the resolution of the progress bar.
_STREAM_POLL_SECONDS = 1.0
# Comment frame so an idle stream does not look like a dead one.
_STREAM_KEEPALIVE_SECONDS = 15.0


class JobCreateRequest(BaseModel):
    source: FromConfig
    targets: list[ToConfig]
    recurring: bool = False
    interval_minutes: int | None = None
    dry_run: bool = False


def _job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "source_config": json.loads(job.source_config),
        "targets_config": json.loads(job.targets_config),
        "recurring": job.recurring,
        "interval_minutes": job.interval_minutes,
        "run_at": job.run_at.isoformat() if job.run_at else None,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "tracks_fetched": job.tracks_fetched,
        "tracks_ok": job.tracks_ok,
        "tracks_err": job.tracks_err,
        "tracks_skipped": job.tracks_skipped,
        "dry_run": job.dry_run,
        "cancel_requested": job.cancel_requested,
    }


def _canonical_key(source_json: str, targets_json: str) -> str:
    """Stable string for deduplication."""
    source = json.loads(source_json)
    targets = sorted(json.loads(targets_json), key=lambda t: json.dumps(t, sort_keys=True))
    return json.dumps({"source": source, "targets": targets}, sort_keys=True)


@router.post("/jobs", status_code=201)
async def create_job(
    body: JobCreateRequest,
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Queue a new relay job. Returns 409 if an equivalent active job exists."""
    if body.dry_run and body.recurring:
        raise HTTPException(status_code=400, detail="dry_run and recurring cannot both be true")

    source_json = _source_adapter.dump_json(body.source).decode()
    targets_json = _targets_adapter.dump_json(body.targets).decode()
    new_key = _canonical_key(source_json, targets_json)

    # Duplicate check — skip entirely if either side is a dry-run job
    if not body.dry_run:
        result = await db.execute(
            select(Job).where(Job.status.in_(list(_ACTIVE_STATUSES)), Job.dry_run.is_(False))
        )
        active_jobs = result.scalars().all()
        for existing in active_jobs:
            existing_key = _canonical_key(existing.source_config, existing.targets_config)
            if existing_key == new_key:
                raise HTTPException(
                    status_code=409,
                    detail=f"An equivalent job is already {existing.status} (id={existing.id})",
                )

    job = Job(
        source_config=source_json,
        targets_config=targets_json,
        recurring=body.recurring,
        interval_minutes=body.interval_minutes,
        dry_run=body.dry_run,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    logger.info("Job queued: %s", job.id)
    return _job_to_dict(job)


@router.get("/jobs")
async def list_jobs(
    db: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    """List all jobs, newest first."""
    result = await db.execute(select(Job).order_by(Job.created_at.desc()))
    jobs = result.scalars().all()
    return [_job_to_dict(j) for j in jobs]


@router.delete("/jobs/{job_id}", status_code=200)
async def cancel_job(
    job_id: str,
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Cancel a queued, scheduled or running job.

    A queued or scheduled job is cancelled outright. A running one cannot be
    killed without risking a write it never recorded, so it is asked to stop at
    the next track boundary and keeps status ``running`` until it does:
    ``cancel_requested`` is what the UI shows meanwhile.
    """
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status == "running":
        if not job.cancel_requested:
            job.cancel_requested = True
            db.add(job)
            await db.commit()
            await db.refresh(job)
            logger.info("Job %s asked to stop at the next track boundary", job_id)
        return _job_to_dict(job)

    if job.status not in {"queued", "scheduled"}:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel a job with status '{job.status}'",
        )
    job.status = "cancelled"
    job.completed_at = datetime.now(timezone.utc)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return _job_to_dict(job)


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Get a single job with its full track event history."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    te_result = await db.execute(
        select(TrackEvent)
        .where(TrackEvent.job_id == job_id)
        .order_by(TrackEvent.created_at.asc())
    )
    events = te_result.scalars().all()
    d = _job_to_dict(job)
    d["events"] = [
        {
            "artist": e.artist,
            "title": e.title,
            "target": e.target_service,
            "success": e.success,
            "error": e.error,
        }
        for e in events
    ]
    return d


@router.get("/jobs/{job_id}/stream")
async def stream_job(
    job_id: str,
    db: AsyncSession = Depends(get_async_session),
) -> StreamingResponse:
    """SSE stream of a job's events, tailed from the database.

    The worker writes; this reads. Nothing is held in process memory, so a
    restarted API, a second worker process, or a browser that reconnects halfway
    all see the same thing: the rows that exist, in order, then live ones as
    they land.
    """
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    async def generate() -> AsyncGenerator[str, None]:
        # ``track_event`` is append-only, so a count is a stable cursor and a
        # row is never yielded twice.
        seen = 0
        last_state: tuple | None = None
        idle_polls = 0
        keepalive_polls = max(1, int(_STREAM_KEEPALIVE_SECONDS / _STREAM_POLL_SECONDS))

        while True:
            async with session_maker() as poll:
                job_row = (
                    await poll.execute(select(Job).where(Job.id == job_id))
                ).scalars().first()
                if job_row is None:
                    return
                rows = (
                    await poll.execute(
                        select(TrackEvent)
                        .where(TrackEvent.job_id == job_id)
                        .order_by(TrackEvent.created_at.asc(), TrackEvent.id.asc())
                        .offset(seen)
                    )
                ).scalars().all()

            for event in rows:
                yield _sse(
                    "track",
                    {
                        "artist": event.artist,
                        "title": event.title,
                        "target": event.target_service,
                        "success": event.success,
                        "error": event.error,
                        "outcome": event.outcome,
                        "reason": event.reason,
                        "candidates": json.loads(event.candidates) if event.candidates else [],
                    },
                )
            seen += len(rows)

            state = (
                job_row.status,
                job_row.tracks_fetched,
                job_row.tracks_ok,
                job_row.tracks_err,
                job_row.tracks_skipped,
                job_row.cancel_requested,
            )
            if rows or state != last_state:
                last_state = state
                idle_polls = 0
                yield _sse(
                    "status",
                    {
                        "status": job_row.status,
                        "tracks_fetched": job_row.tracks_fetched,
                        "tracks_ok": job_row.tracks_ok,
                        "tracks_err": job_row.tracks_err,
                        "tracks_skipped": job_row.tracks_skipped,
                        "cancel_requested": job_row.cancel_requested,
                    },
                )
            else:
                idle_polls += 1

            if job_row.status in _TERMINAL_STATUSES:
                yield _sse(
                    "done",
                    {
                        "status": job_row.status,
                        "tracks_fetched": job_row.tracks_fetched,
                        "tracks_ok": job_row.tracks_ok,
                        "tracks_err": job_row.tracks_err,
                        "tracks_skipped": job_row.tracks_skipped,
                    },
                )
                return

            if idle_polls >= keepalive_polls:
                idle_polls = 0
                yield ": keepalive\n\n"

            await asyncio.sleep(_STREAM_POLL_SECONDS)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
