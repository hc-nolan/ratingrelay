"""The relay pipeline: match, reconcile, write — in one place.

The pipeline owns four decisions so that nothing above it has to repeat them:

* whether a write was already made *in the state the source now wants* (the sync
  store records the intent, not just the key),
* whether a plan gets committed (``dry_run`` is the only difference),
* whether a recorded write has been withdrawn at the source and must be undone,
* what an outcome is worth reporting (typed, never a string to be re-parsed).

Everything service-specific lives behind the :class:`Source` and :class:`Target`
protocols, so the same pipeline serves the API, the job worker and the CLI.

Every network call the pipeline makes — fetch, plan, commit, undo — goes through
:func:`ratingrelay.engine.retry.attempt`, which retries a transport failure a
bounded number of times (see that module for why only ``OSError`` is retried).
"""

from typing import Optional, Sequence

from ratingrelay.engine.plan import WritePlan
from ratingrelay.engine.retry import attempt
from ratingrelay.engine.services import Source, Target
from ratingrelay.engine.store import ProgressSink, SyncedTrack, SyncStore
from ratingrelay.engine.types import (
    OutcomeStatus,
    Reason,
    RunReport,
    TargetReport,
    Track,
    TrackOutcome,
)
from ratingrelay.settings import logger


class _NoProgress:
    """Progress sink for a run with nothing watching it."""

    async def fetch_complete(self, count: int) -> None:
        pass

    async def outcome(self, outcome: TrackOutcome) -> None:
        pass


NO_PROGRESS: ProgressSink = _NoProgress()


def _intent_key(plan: WritePlan) -> Optional[str]:
    return plan.intent.key if plan.intent else None


def _with_known_mbid(track: Track, record: Optional[SyncedTrack]) -> Track:
    """Reuse the MBID we already resolved, so a rerun makes no lookup."""
    if record is None or track.recording_mbid or not record.recording_mbid:
        return track
    return Track(
        artist=track.artist,
        title=track.title,
        recording_mbid=record.recording_mbid,
        track_mbid=track.track_mbid,
        rating=track.rating,
        external_id=track.external_id,
    )


async def _apply(
    track: Track,
    target: Target,
    *,
    synced: dict[str, SyncedTrack],
    sync_store: SyncStore,
    source_service: str,
    dry_run: bool,
) -> TrackOutcome:
    """Decide and (unless dry run) perform the write for one track on one target."""
    record = synced.get(track.key)
    try:
        plan: WritePlan = await attempt(
            lambda: target.plan(_with_known_mbid(track, record)),
            f"{target.service} plan for '{track.title}'",
        )
    except OSError as exc:
        # A target we cannot ask is this track's failure, not the run's.
        logger.error("Planning against %s failed for '%s': %s", target.service, track.title, exc)
        return TrackOutcome(
            track, target.service, OutcomeStatus.FAILED, Reason.WRITE_FAILED, str(exc)
        )

    # The same intent this target already holds is what "already synced" means
    # now: a changed rating or a withdrawn love is a different intent, and gets
    # written again.
    if record is not None and plan.intent is not None and record.intent == plan.intent.key:
        return plan.to_outcome(OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED, None)

    if plan.status is not OutcomeStatus.WRITTEN:
        return plan.to_outcome()
    if not plan.actionable:
        return plan.to_outcome(
            OutcomeStatus.FAILED,
            Reason.WRITE_FAILED,
            f"{target.service} produced no write for this track",
        )

    outcome = plan.to_outcome()
    if dry_run:
        return outcome

    try:
        await attempt(
            plan.commit, f"{target.service} write for '{track.title}'"
        )
    except Exception as exc:  # noqa: BLE001 - one failed write must not stop the run
        logger.error("Write to %s failed for '%s': %s", target.service, track.title, exc)
        return plan.to_outcome(OutcomeStatus.FAILED, Reason.WRITE_FAILED, str(exc))

    await sync_store.remember(
        source_service,
        target.service,
        SyncedTrack(
            key=track.key,
            recording_mbid=plan.recording_mbid,
            intent=_intent_key(plan),
            rating=plan.intent.rating if plan.intent else None,
            target_key=plan.target_key,
        ),
    )
    return outcome


async def _undo(
    record: SyncedTrack,
    target: Target,
    *,
    sync_store: SyncStore,
    source_service: str,
    dry_run: bool,
) -> TrackOutcome:
    """Revert a write the source no longer wants, or say why we cannot.

    The undoability check runs before the dry-run shortcut: a dry run must make the
    promise a real run keeps, so a record we cannot undo is reported as skipped in
    both, never as a revert that would not happen.
    """
    track = record.track
    try:
        target.undoable(record)
    except ValueError as exc:
        # The record predates target_key, so we know we wrote here but not what:
        # clearing it could remove a rating the user made themselves.
        logger.warning(
            "Cannot undo %s write for '%s': %s", target.service, record.key, exc
        )
        return TrackOutcome(
            track,
            target.service,
            OutcomeStatus.SKIPPED,
            Reason.UNRECORDED_TARGET,
            str(exc),
        )

    if dry_run:
        return TrackOutcome(
            track, target.service, OutcomeStatus.REVERTED, Reason.WITHDRAWN
        )

    try:
        await attempt(
            lambda: target.undo(record),
            f"{target.service} undo for '{record.key}'",
        )
    except ValueError as exc:  # the record changed under us; same promise
        return TrackOutcome(
            track, target.service, OutcomeStatus.SKIPPED, Reason.UNRECORDED_TARGET, str(exc)
        )
    except Exception as exc:  # noqa: BLE001 - one failed undo must not stop the run
        logger.error("Undo on %s failed for '%s': %s", target.service, record.key, exc)
        return TrackOutcome(
            track, target.service, OutcomeStatus.FAILED, Reason.WRITE_FAILED, str(exc)
        )

    await sync_store.forget(source_service, target.service, record.key)
    return TrackOutcome(track, target.service, OutcomeStatus.REVERTED, Reason.WITHDRAWN)


async def _reconcile(
    target: Target,
    *,
    synced: dict[str, SyncedTrack],
    seen: set[str],
    sync_store: SyncStore,
    source_service: str,
    dry_run: bool,
) -> list[TrackOutcome]:
    """Undo the writes whose track the source has stopped rating.

    Only rows we recorded are considered, and inside them only rows that say
    what was written — that is the whole of "only undo our own writes".
    """
    outcomes = []
    for key, record in synced.items():
        if key in seen:
            continue
        outcomes.append(
            await _undo(
                record,
                target,
                sync_store=sync_store,
                source_service=source_service,
                dry_run=dry_run,
            )
        )
    return outcomes


async def run_tracks(
    tracks: Sequence[Track],
    targets: Sequence[Target],
    *,
    source_service: str,
    sync_store: SyncStore,
    progress: ProgressSink = NO_PROGRESS,
    dry_run: bool = False,
) -> RunReport:
    """Relay an already-fetched track list to every target, then reconcile.

    *source_service* is required and keyword-only: it is half of the key the
    sync store is written under, so a run that guessed it ("unknown") would
    write records it could never reconcile against.
    """
    outcomes: list[TrackOutcome] = []
    seen = {track.key for track in tracks}

    for target in targets:
        synced = await sync_store.load(source_service, target.service)
        for track in tracks:
            outcome = await _apply(
                track,
                target,
                synced=synced,
                sync_store=sync_store,
                source_service=source_service,
                dry_run=dry_run,
            )
            outcomes.append(outcome)
            await progress.outcome(outcome)

        for outcome in await _reconcile(
            target,
            synced=synced,
            seen=seen,
            sync_store=sync_store,
            source_service=source_service,
            dry_run=dry_run,
        ):
            outcomes.append(outcome)
            await progress.outcome(outcome)

    reports = tuple(
        TargetReport.from_outcomes(target.service, tuple(outcomes)) for target in targets
    )
    return RunReport(
        source=source_service,
        tracks_fetched=len(tracks),
        targets=reports,
        outcomes=tuple(outcomes),
        dry_run=dry_run,
    )


async def run(
    source: Source,
    targets: Sequence[Target],
    *,
    sync_store: SyncStore,
    progress: ProgressSink = NO_PROGRESS,
    dry_run: bool = False,
) -> RunReport:
    """Fetch from *source*, then relay to every target."""
    tracks = await attempt(
        source.fetch, f"{source.service} fetch"
    )
    await progress.fetch_complete(len(tracks))
    logger.info("Relay: fetched %d tracks from %s", len(tracks), source.service)
    return await run_tracks(
        tracks,
        targets,
        sync_store=sync_store,
        source_service=source.service,
        progress=progress,
        dry_run=dry_run,
    )
