"""
Background worker that processes queued jobs one at a time.

Progress is not pushed anywhere: every track event is written to the database
as it happens, and readers (the SSE stream, the job page) tail those rows. That
is what lets the worker run in a different process from the API, or with no API
at all, which is the whole point of the headless target.

A running job is cancelled cooperatively: ``DELETE /api/relay/jobs/{id}`` sets
``job.cancel_requested``, and the worker checks that flag at each track boundary
(after the track's event and sync record are written) and stops there.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.relay import execute_relay
from ratingrelay.db import session_maker
from ratingrelay.engine.types import TrackOutcome
from ratingrelay.models.jobs import Job, TrackEvent
from ratingrelay.schemas.relay import source_adapter as _source_adapter, targets_adapter as _targets_adapter
from ratingrelay.settings import logger

# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class RunCancelled(Exception):
    """Raised at a track boundary when the job's cancel flag is set."""


async def _cancel_requested(db: AsyncSession, job_id: str) -> bool:
    """Read the flag from the database, not from the identity map.

    The API request that sets it runs in another session, so ``job`` in this
    session would keep reporting the value it loaded at the start of the run.
    """
    result = await db.execute(select(Job.cancel_requested).where(Job.id == job_id))
    return bool(result.scalar_one_or_none())


def _candidates_json(outcome: Optional[TrackOutcome]) -> Optional[str]:
    """The candidates attached to an outcome, as JSON for the UI.

    ``recording_mbid`` rides along because it is what makes a ListenBrainz
    candidate resolvable: feedback is keyed by MBID, so a name alone cannot be
    turned into a mapping without looking the recording up again.
    """
    if outcome is None or not outcome.matches:
        return None
    return json.dumps(
        [
            {
                "artist": match.artist,
                "title": match.title,
                "recording_mbid": match.recording_mbid,
            }
            for match in outcome.matches
        ]
    )


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

        # Local counters (updated per track, flushed to DB at end)
        tracks_ok = 0
        tracks_err = 0
        tracks_skipped = 0

        # 2. on_fetch_complete callback
        async def on_fetch_complete(count: int) -> None:
            job.tracks_fetched = count
            db.add(job)
            await db.commit()
            # Cancelling during the fetch is only observable here: the fetch is
            # a single await inside the source.
            if await _cancel_requested(db, job_id):
                raise RunCancelled

        # 3. on_track callback
        async def on_track(
            track: dict,
            target: str,
            success: bool,
            error: Optional[str],
            skipped: bool = False,
            outcome: Optional[TrackOutcome] = None,
        ) -> None:
            nonlocal tracks_ok, tracks_err, tracks_skipped

            event = TrackEvent(
                job_id=job_id,
                artist=track.get("artist", ""),
                title=track.get("title", ""),
                target_service=target,
                success=success,
                error=error,
                outcome=outcome.status.value if outcome else None,
                reason=outcome.reason.value if outcome and outcome.reason else None,
                candidates=_candidates_json(outcome),
            )
            db.add(event)

            if not skipped:
                if success:
                    tracks_ok += 1
                else:
                    tracks_err += 1
            else:
                tracks_skipped += 1

            # Commit per track. Two reasons: the events are the thing the stream
            # tails, so they must be visible outside this session while the run
            # is still going, and a crash mid-run should not lose the history of
            # the writes that did happen.
            job.tracks_ok = tracks_ok
            job.tracks_err = tracks_err
            job.tracks_skipped = tracks_skipped
            db.add(job)
            await db.commit()

            # The flag is checked *after* the track's event and sync record are
            # written, so stopping never loses a write we really made.
            if await _cancel_requested(db, job_id):
                raise RunCancelled

        try:
            # 4. Parse configs
            source = _source_adapter.validate_json(job.source_config)
            targets = _targets_adapter.validate_json(job.targets_config)

            # 5. Execute
            result = await execute_relay(db, source, targets, on_fetch_complete, on_track, job.dry_run)

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

            # Schedule next run if recurring (never for dry-run jobs)
            if job.recurring and job.interval_minutes and not job.dry_run:
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

        except RunCancelled:
            # The counters are the ones actually reached; the writes behind them
            # are already in the sync store, so the next run resumes from here.
            job.status = "cancelled"
            job.tracks_ok = tracks_ok
            job.tracks_err = tracks_err
            job.tracks_skipped = tracks_skipped
            job.completed_at = datetime.now(timezone.utc)
            db.add(job)
            await db.commit()
            logger.info(
                "Worker: job %s cancelled after %d ok / %d err",
                job_id,
                tracks_ok,
                tracks_err,
            )

        except Exception as exc:
            logger.exception("Worker: job %s failed: %s", job_id, exc)
            job.status = "failed"
            job.completed_at = datetime.now(timezone.utc)
            db.add(job)
            await db.commit()


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
                # A cancel that arrived while the process was dying still means
                # the user asked for it to stop, so it must not resume.
                j.status = "cancelled" if j.cancel_requested else "queued"
                if j.status == "cancelled":
                    j.completed_at = datetime.now(timezone.utc)
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
