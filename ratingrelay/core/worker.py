"""
Background worker that processes queued jobs one at a time.

SSE listeners are stored in memory; the worker publishes events to them
as each track is processed.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.relay import execute_relay
from ratingrelay.db import session_maker
from ratingrelay.models.jobs import Job, TrackEvent
from ratingrelay.schemas.relay import FromConfig, ToConfig
from ratingrelay.settings import logger

# ---------------------------------------------------------------------------
# In-memory SSE listener registry
# ---------------------------------------------------------------------------

_listeners: dict[str, list[asyncio.Queue]] = {}

_source_adapter: TypeAdapter = TypeAdapter(FromConfig)
_targets_adapter: TypeAdapter = TypeAdapter(list[ToConfig])


def subscribe(job_id: str) -> "asyncio.Queue[dict]":
    q: asyncio.Queue[dict] = asyncio.Queue()
    _listeners.setdefault(job_id, []).append(q)
    return q


def unsubscribe(job_id: str, q: "asyncio.Queue[dict]") -> None:
    listeners = _listeners.get(job_id, [])
    try:
        listeners.remove(q)
    except ValueError:
        pass
    if not listeners:
        _listeners.pop(job_id, None)


async def broadcast(job_id: str, event_type: str, data: dict) -> None:
    listeners = _listeners.get(job_id, [])
    payload = {"event": event_type, "data": data}
    for q in list(listeners):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------


async def run_job(job_id: str) -> None:
    async with session_maker() as db:
        # 1. Load job and mark as running
        job = await db.get(Job, job_id)
        if job is None:
            logger.error("Worker: job %s not found", job_id)
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        db.add(job)
        await db.commit()
        await db.refresh(job)

        await broadcast(
            job_id,
            "status",
            {
                "status": job.status,
                "tracks_fetched": job.tracks_fetched,
                "tracks_ok": job.tracks_ok,
                "tracks_err": job.tracks_err,
            },
        )

        # Local counters (updated per track, flushed to DB at end)
        tracks_ok = 0
        tracks_err = 0
        tracks_skipped = 0

        # 2. on_fetch_complete callback
        async def on_fetch_complete(count: int) -> None:
            job.tracks_fetched = count
            db.add(job)
            await db.commit()
            await broadcast(
                job_id,
                "status",
                {
                    "status": job.status,
                    "tracks_fetched": count,
                    "tracks_ok": tracks_ok,
                    "tracks_err": tracks_err,
                },
            )

        # 3. on_track callback
        async def on_track(
            track: dict, target: str, success: bool, error: Optional[str]
        ) -> None:
            nonlocal tracks_ok, tracks_err

            event = TrackEvent(
                job_id=job_id,
                artist=track.get("artist", ""),
                title=track.get("title", ""),
                target_service=target,
                success=success,
                error=error,
            )
            db.add(event)
            await db.commit()

            if success:
                tracks_ok += 1
            else:
                tracks_err += 1

            await broadcast(
                job_id,
                "track",
                {
                    "artist": event.artist,
                    "title": event.title,
                    "target": target,
                    "success": success,
                    "error": error,
                },
            )
            await broadcast(
                job_id,
                "progress",
                {"tracks_ok": tracks_ok, "tracks_err": tracks_err},
            )

        try:
            # 4. Parse configs
            source = _source_adapter.validate_json(job.source_config)
            targets = _targets_adapter.validate_json(job.targets_config)

            # 5. Execute
            result = await execute_relay(db, source, targets, on_fetch_complete, on_track)

            # 6. Determine final status
            total_ok = sum(r["ok"] for r in result["results"])
            total_err = sum(r["err"] for r in result["results"])
            total_skipped = sum(r.get("skipped", 0) for r in result["results"])
            if total_ok == 0 and total_err > 0:
                final_status = "failed"
            elif total_err > 0:
                final_status = "partial"
            else:
                final_status = "completed"

            job.status = final_status
            job.tracks_ok = total_ok
            job.tracks_err = total_err
            job.tracks_skipped = total_skipped
            job.completed_at = datetime.now(timezone.utc)
            db.add(job)
            await db.commit()

            # Schedule next run if recurring
            if job.recurring and job.interval_minutes:
                next_run = datetime.now(timezone.utc) + timedelta(
                    minutes=job.interval_minutes
                )
                next_job = Job(
                    status="scheduled",
                    source_config=job.source_config,
                    targets_config=job.targets_config,
                    recurring=True,
                    interval_minutes=job.interval_minutes,
                    run_at=next_run,
                )
                db.add(next_job)
                await db.commit()
                logger.info(
                    "Worker: scheduled next run for job %s at %s", job_id, next_run
                )

        except Exception as exc:
            logger.exception("Worker: job %s failed: %s", job_id, exc)
            job.status = "failed"
            job.completed_at = datetime.now(timezone.utc)
            db.add(job)
            await db.commit()

        # 8. Broadcast done
        await db.refresh(job)
        await broadcast(
            job_id,
            "done",
            {
                "status": job.status,
                "tracks_fetched": job.tracks_fetched,
                "tracks_ok": job.tracks_ok,
                "tracks_err": job.tracks_err,
                "tracks_skipped": job.tracks_skipped,
            },
        )


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------


async def worker_loop() -> None:
    """
    Continuously process queued jobs one at a time.

    On startup: reset any jobs stuck in "running" back to "queued".
    Each iteration:
      1. Promote scheduled jobs whose run_at <= now to "queued".
      2. Pick the oldest queued job.
      3. If found: run it (blocks until complete).
      4. If not found: sleep 2 seconds.
    """
    # Reset stale running jobs
    try:
        async with session_maker() as db:
            result = await db.execute(
                select(Job).where(Job.status == "running")
            )
            stale = result.scalars().all()
            for j in stale:
                j.status = "queued"
                db.add(j)
            if stale:
                await db.commit()
                logger.info("Worker: reset %d stale running jobs to queued", len(stale))
    except Exception as exc:
        logger.exception("Worker: error resetting stale jobs: %s", exc)

    while True:
        try:
            async with session_maker() as db:
                now = datetime.now(timezone.utc)

                # Promote scheduled jobs whose time has come
                sched_result = await db.execute(
                    select(Job).where(
                        Job.status == "scheduled",
                        Job.run_at <= now,
                    )
                )
                due = sched_result.scalars().all()
                for j in due:
                    j.status = "queued"
                    db.add(j)
                if due:
                    await db.commit()
                    logger.info("Worker: promoted %d scheduled jobs to queued", len(due))

                # Pick oldest queued job
                queued_result = await db.execute(
                    select(Job)
                    .where(Job.status == "queued")
                    .order_by(Job.created_at.asc())
                    .limit(1)
                )
                next_job = queued_result.scalars().first()

            if next_job is not None:
                logger.info("Worker: starting job %s", next_job.id)
                await run_job(next_job.id)
            else:
                await asyncio.sleep(2)

        except asyncio.CancelledError:
            logger.info("Worker: loop cancelled, shutting down")
            break
        except Exception as exc:
            logger.exception("Worker: unexpected error in loop: %s", exc)
            await asyncio.sleep(5)
