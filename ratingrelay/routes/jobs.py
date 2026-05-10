"""
Job queue API routes.

POST   /api/relay/jobs           — queue a new job
GET    /api/relay/jobs           — list all jobs, newest first
DELETE /api/relay/jobs/{id}      — cancel a queued/scheduled job
GET    /api/relay/jobs/{id}/stream — SSE stream of job events
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.worker import broadcast, subscribe, unsubscribe
from ratingrelay.db import get_async_session
from ratingrelay.models.jobs import Job, TrackEvent
from ratingrelay.schemas.relay import FromConfig, ToConfig
from ratingrelay.settings import logger

router = APIRouter(prefix="/api/relay", tags=["jobs"])

_source_adapter: TypeAdapter = TypeAdapter(FromConfig)
_targets_adapter: TypeAdapter = TypeAdapter(list[ToConfig])

_TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled"}
_ACTIVE_STATUSES = {"queued", "running", "scheduled"}


class JobCreateRequest(BaseModel):
    source: FromConfig
    targets: list[ToConfig]
    recurring: bool = False
    interval_minutes: int | None = None


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
    source_json = _source_adapter.dump_json(body.source).decode()
    targets_json = _targets_adapter.dump_json(body.targets).decode()
    new_key = _canonical_key(source_json, targets_json)

    # Duplicate check
    result = await db.execute(
        select(Job).where(Job.status.in_(list(_ACTIVE_STATUSES)))
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
    """Cancel a queued or scheduled job."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
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

    await broadcast(
        job_id,
        "done",
        {
            "status": "cancelled",
            "tracks_fetched": job.tracks_fetched,
            "tracks_ok": job.tracks_ok,
            "tracks_err": job.tracks_err,
        },
    )
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
    """SSE stream of events for a job."""
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Load past track events
    te_result = await db.execute(
        select(TrackEvent)
        .where(TrackEvent.job_id == job_id)
        .order_by(TrackEvent.created_at.asc())
    )
    past_events = te_result.scalars().all()

    # Snapshot job state for use in the generator
    job_dict = _job_to_dict(job)
    is_terminal = job.status in _TERMINAL_STATUSES

    async def generate() -> AsyncGenerator[str, None]:
        # Initial status event
        yield _sse("status", {
            "status": job_dict["status"],
            "tracks_fetched": job_dict["tracks_fetched"],
            "tracks_ok": job_dict["tracks_ok"],
            "tracks_err": job_dict["tracks_err"],
            "tracks_skipped": job_dict["tracks_skipped"],
        })

        # Replay past track events
        for te in past_events:
            yield _sse("track", {
                "artist": te.artist,
                "title": te.title,
                "target": te.target_service,
                "success": te.success,
                "error": te.error,
            })

        if is_terminal:
            yield _sse("done", {
                "status": job_dict["status"],
                "tracks_fetched": job_dict["tracks_fetched"],
                "tracks_ok": job_dict["tracks_ok"],
                "tracks_err": job_dict["tracks_err"],
            })
            return

        # Subscribe to live events
        q = subscribe(job_id)
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield _sse(payload["event"], payload["data"])
                    if payload["event"] == "done":
                        break
                except asyncio.TimeoutError:
                    # Keepalive comment
                    yield ": keepalive\n\n"
        finally:
            unsubscribe(job_id, q)

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
