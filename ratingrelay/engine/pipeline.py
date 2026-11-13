"""The relay pipeline: match, reconcile, write — in one place.

The pipeline owns three decisions so that nothing above it has to repeat them:

* whether a track was already written (the sync store says so),
* whether a plan gets committed (``dry_run`` is the only difference),
* what an outcome is worth reporting (typed, never a string to be re-parsed).

Everything service-specific lives behind the :class:`Source` and :class:`Target`
protocols, so the same pipeline serves the API, the job worker and the CLI.
"""

from typing import Optional, Sequence

from ratingrelay.engine.plan import WritePlan
from ratingrelay.engine.services import Source, Target
from ratingrelay.engine.store import ProgressSink, SyncStore, SyncedTrack
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
    if track.key in synced:
        return TrackOutcome(track, target.service, OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED)

    plan: WritePlan = await target.plan(track)
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
        await plan.commit()
    except Exception as exc:  # noqa: BLE001 - one failed write must not stop the run
        logger.error("Write to %s failed for '%s': %s", target.service, track.title, exc)
        return plan.to_outcome(OutcomeStatus.FAILED, Reason.WRITE_FAILED, str(exc))

    await sync_store.remember(
        source_service,
        target.service,
        SyncedTrack(track.key, plan.recording_mbid, _intent_key(plan), track.rating),
    )
    return outcome


def _intent_key(plan: WritePlan) -> Optional[str]:
    return plan.intent.key if plan.intent else None


async def run_tracks(
    tracks: Sequence[Track],
    targets: Sequence[Target],
    *,
    sync_store: SyncStore,
    source_service: str = "unknown",
    progress: ProgressSink = NO_PROGRESS,
    dry_run: bool = False,
) -> RunReport:
    """Relay an already-fetched track list to every target."""
    outcomes: list[TrackOutcome] = []
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
    tracks = await source.fetch()
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
