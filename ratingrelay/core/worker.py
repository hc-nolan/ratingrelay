"""
Background worker that processes queued jobs one at a time.

Progress is not pushed anywhere: every track event is written to the database
as it happens, and readers (the SSE stream, the job page) tail those rows. That
is what lets the worker run in a different process from the API, or with no API
at all, which is the whole point of the headless target.

A running job is cancelled cooperatively: ``DELETE /api/relay/jobs/{id}`` sets
``job.cancel_requested``, and the worker checks that flag at each track boundary
(after the track's event and sync record are written) and stops there.

A job whose worker died is resumed, not repeated. The writes it made are in the
sync store and its per-track events are in the journal, so a second attempt starts
from those counters and does not re-journal the tracks the first attempt already
reported (an ``already_synced`` outcome on a resumed attempt is the echo of work
that was done before the crash, not work that just happened).
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import asc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.relay import execute_relay
from ratingrelay.db import session_maker
from ratingrelay.engine.types import Reason, TrackOutcome
from ratingrelay.models.jobs import Job, TrackEvent
from ratingrelay.schemas.relay import source_adapter as _source_adapter
from ratingrelay.schemas.relay import targets_adapter as _targets_adapter
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


async def _claim(db: AsyncSession, job: Job) -> bool:
    """Mark *job* running, but only if it is still queued.

    A cancel that landed after the loop picked the job is the newer decision,
    and an unconditional write of ``running`` would overwrite it.
    """
    result = await db.execute(
        update(Job).where(Job.id == job.id, Job.status == "queued").values(status="running")
    )
    await db.commit()
    await db.refresh(job)
    return result.rowcount == 1


async def _schedule_next(db: AsyncSession, job: Job) -> None:
    """Queue the next run of a recurring relay (never for a dry run)."""
    if not (job.recurring and job.interval_minutes and not job.dry_run):
        return
    next_run = datetime.now(timezone.utc) + timedelta(minutes=job.interval_minutes)
    db.add(
        Job(
            status="scheduled",
            source_config=job.source_config,
            targets_config=job.targets_config,
            recurring=True,
            interval_minutes=job.interval_minutes,
            run_at=next_run,
            # The drift policy belongs to the relay, so it survives the night;
            # the mass-undo override does not, by design.
            reapply_target_drift=job.reapply_target_drift,
        )
    )
    await db.commit()
    logger.info("Worker: scheduled next run for job %s at %s", job.id, next_run)


@dataclass
class _Tally:
    """The job's three counters, flushed to its row as the run goes."""

    ok: int = 0
    err: int = 0
    skipped: int = 0

    @classmethod
    def continuing(cls, job: Job, resuming: bool) -> "_Tally":
        """A resumed attempt continues the counters, or the totals would shrink."""
        if not resuming:
            return cls()
        return cls(job.tracks_ok, job.tracks_err, job.tracks_skipped)

    def count(self, success: bool, skipped: bool) -> None:
        if skipped:
            self.skipped += 1
        elif success:
            self.ok += 1
        else:
            self.err += 1

    def flush(self, job: Job) -> None:
        job.tracks_ok, job.tracks_err, job.tracks_skipped = self.ok, self.err, self.skipped

    @property
    def status(self) -> str:
        """Nothing but failures is a failed job. A run that converged anything
        (wrote, reverted or found it already in sync) got through, so its errors
        make it partial."""
        if self.ok == 0 and self.skipped == 0 and self.err > 0:
            return "failed"
        return "partial" if self.err > 0 else "completed"


async def _finish(db: AsyncSession, job: Job, status: str, tally: Optional[_Tally]) -> None:
    job.status = status
    if tally is not None:
        tally.flush(job)
    job.completed_at = datetime.now(timezone.utc)
    db.add(job)
    await db.commit()


async def run_job(job_id: str) -> None:
    async with session_maker() as db:
        # 1. Load job and mark as running
        job = await db.get(Job, job_id)
        if job is None:
            logger.error("Worker: job %s not found", job_id)
            return
        if not await _claim(db, job):
            logger.info("Worker: job %s is %s, not queued; skipping", job_id, job.status)
            return

        # A job that already started is being retried after its worker died.
        # Its start time stays the first attempt's: it is one run.
        resuming = job.started_at is not None
        if not resuming:
            job.started_at = datetime.now(timezone.utc)
        db.add(job)
        await db.commit()
        await db.refresh(job)

        tally = _Tally.continuing(job, resuming)

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
            if (
                resuming
                and outcome is not None
                and outcome.reason is Reason.ALREADY_SYNCED
            ):
                # The attempt that did this work already reported it. Journalling
                # it again would show every track twice and count the library a
                # second time.
                if await _cancel_requested(db, job_id):
                    raise RunCancelled
                return

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

            tally.count(success, skipped)

            # Commit per track. Two reasons: the events are the thing the stream
            # tails, so they must be visible outside this session while the run
            # is still going, and a crash mid-run should not lose the history of
            # the writes that did happen.
            tally.flush(job)
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
            await execute_relay(
                db,
                source,
                targets,
                on_fetch_complete,
                on_track,
                job.dry_run,
                allow_mass_undo=job.allow_mass_undo,
                reapply_drift=job.reapply_target_drift,
            )

            # 6. Final status from the counters, which on a resumed attempt
            # span both tries.
            await _finish(db, job, tally.status, tally)
            await _schedule_next(db, job)

        except RunCancelled:
            # The counters are the ones actually reached; the writes behind them
            # are already in the sync store, so the next run resumes from here.
            await _finish(db, job, "cancelled", tally)
            logger.info(
                "Worker: job %s cancelled after %d ok / %d err", job_id, tally.ok, tally.err
            )

        # The job fails, not the worker.
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Worker: job %s failed: %s", job_id, exc)
            await _finish(db, job, "failed", None)
            # One bad night (a timeout, Plex restarting) must not end the
            # series: the next run is as likely to work as this one was not.
            # A cancel does end it — that is how a user stops a recurring relay.
            await _schedule_next(db, job)


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
                logger.info(
                    "Worker: %d job(s) were interrupted by a restart and will resume",
                    len(stale),
                )
    # The queue still has to start.
    except Exception as exc:  # pylint: disable=broad-exception-caught
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
                    .order_by(asc(Job.created_at))
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
        # The loop must outlive one bad poll.
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Worker: unexpected error in loop: %s", exc)
            await asyncio.sleep(5)
