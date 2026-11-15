"""The relay entry point the app calls: fetch from a source, write to targets.

The logic lives in :mod:`ratingrelay.engine`; this module is the translation
layer it replaced. It still speaks the shapes the router and the job worker
already use — pydantic request configs in, dicts and a ``(ok, err, skipped)``
triple out — so those callers did not have to change in the same commit.

New code should call the engine directly. What is left here is what the typed
outcomes do not yet express: the display strings behind ``on_track``'s ``error``
argument and the list shape of ``execute_relay``'s result.
"""

from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core import adapters
from ratingrelay.core.credentials import (
    lastfm_credentials,
    listenbrainz_credentials,
    plex_credentials,
)
from ratingrelay.db.store import SqlSyncStore
from ratingrelay.engine import pipeline
from ratingrelay.engine.retry import attempt
from ratingrelay.engine.services.lastfm import LastFmClient
from ratingrelay.engine.services.listenbrainz import ListenBrainzClient
from ratingrelay.engine.services.plex import PlexLibrary
from ratingrelay.engine.store import ProgressSink
from ratingrelay.engine.types import OutcomeStatus, Reason, RunReport, TrackOutcome
from ratingrelay.schemas.relay import (
    LastFMFromConfig,
    LastFMToConfig,
    ListenBrainzFromConfig,
    ListenBrainzToConfig,
    PlexFromConfig,
    PlexToConfig,
    ResetFromConfig,
)
from ratingrelay.settings import logger

# Callback type: (track_dict, target_service_name, success, error_or_None, skipped)
# skipped=True means the track was already synced; success/error are ignored in that case.
# A callback receives the display dict, the target service, whether the target
# now holds what the source wanted, an error string for the UI, whether a dry
# run skipped the write, and the typed outcome behind all of it. The outcome is
# what the API stores: the strings are for humans, the outcome is for logic.
TrackCallback = Callable[
    [dict, str, bool, Optional[str], bool, Optional["TrackOutcome"]], Awaitable[None]
]


async def _noop_fetch(count: int) -> None:
    pass


async def _noop_track(
    track: dict,
    target: str,
    success: bool,
    error: Optional[str],
    skipped: bool = False,
    outcome: Optional[TrackOutcome] = None,
) -> None:
    pass


class CallbackProgress:
    """Translate engine outcomes into the callback the worker and router use.

    Skipped tracks are only reported in a dry run: a real run already reported
    them the first time, and the worker treats every callback as a new event.
    """

    def __init__(
        self,
        on_fetch_complete: Optional[Callable[[int], Awaitable[None]]],
        on_track: TrackCallback,
        dry_run: bool,
    ):
        self._on_fetch_complete = on_fetch_complete
        self._on_track = on_track
        self._dry_run = dry_run

    async def fetch_complete(self, count: int) -> None:
        if self._on_fetch_complete is not None:
            await self._on_fetch_complete(count)

    async def outcome(self, outcome: TrackOutcome) -> None:
        skipped = outcome.status is OutcomeStatus.SKIPPED
        if skipped and not self._dry_run:
            return
        # A revert is a write we made in the other direction: the target is now
        # in the state the source wants, which is what "ok" means here.
        written = outcome.status in (OutcomeStatus.WRITTEN, OutcomeStatus.REVERTED)
        await self._on_track(
            adapters.track_to_dict(outcome.track),
            outcome.target,
            written,
            None if written or skipped else outcome.detail,
            skipped,
            outcome,
        )


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------


async def _fetch(db: AsyncSession, config: object) -> list[dict]:
    source = await adapters.source_for(db, config)
    return [adapters.track_to_dict(track) for track in await source.fetch()]


async def fetch_plex(db: AsyncSession, config: PlexFromConfig) -> list[dict]:
    return await _fetch(db, config)


async def fetch_lastfm(db: AsyncSession, config: LastFMFromConfig) -> list[dict]:
    return await _fetch(db, config)


async def fetch_listenbrainz(
    db: AsyncSession, config: ListenBrainzFromConfig
) -> list[dict]:
    return await _fetch(db, config)


# ---------------------------------------------------------------------------
# Target writers
# ---------------------------------------------------------------------------


def _counts(report: RunReport) -> tuple[int, int, int]:
    """(ok, err, skipped) for a single-target report, in the shape callers expect."""
    target = report.targets[0]
    return target.written + target.reverted, target.unmatched + target.failed, target.skipped


async def _write(
    db: AsyncSession,
    tracks: list[dict],
    config: object,
    source_service: str,
    on_track: TrackCallback,
    dry_run: bool,
) -> tuple[int, int, int]:
    try:
        target = await adapters.target_for(db, config)
    except ValueError as exc:
        for track in tracks:
            await on_track(track, target_service(config), False, str(exc), False, None)
        return 0, len(tracks), 0

    sync_store, _ = adapters.stores(db)
    report = await pipeline.run_tracks(
        [adapters.track_from_dict(track) for track in tracks],
        [target],
        sync_store=sync_store,
        source_service=source_service,
        progress=CallbackProgress(None, on_track, dry_run),
        dry_run=dry_run,
    )
    return _counts(report)


def target_service(config: object) -> str:
    """The service name a request config names, without touching credentials."""
    return str(getattr(config, "service", "unknown"))


async def write_to_plex(
    db: AsyncSession,
    tracks: list[dict],
    config: PlexToConfig,
    source_service: str,
    on_track: TrackCallback = _noop_track,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    return await _write(db, tracks, config, source_service, on_track, dry_run)


async def write_to_lastfm(
    db: AsyncSession,
    tracks: list[dict],
    config: LastFMToConfig,
    source_service: str,
    on_track: TrackCallback = _noop_track,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    return await _write(db, tracks, config, source_service, on_track, dry_run)


async def write_to_listenbrainz(
    db: AsyncSession,
    tracks: list[dict],
    config: ListenBrainzToConfig,
    source_service: str,
    on_track: TrackCallback = _noop_track,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    return await _write(db, tracks, config, source_service, on_track, dry_run)


# ---------------------------------------------------------------------------
# Reset functions
#
# There are two resets, and they are different promises:
#
# * ``_reset_recorded`` (the default) undoes exactly the writes this tool made to
#   the target, record by record, and drops each record it reverts. It cannot
#   touch a rating the user set themselves, because it never looks at the target
#   for things to clear — it looks at what we did.
# * ``_reset_service`` clears every rating on the service. It is a destructive
#   whole-service wipe, so it only runs when the request says ``all``.
# ---------------------------------------------------------------------------


_RESET_TARGET_CONFIGS = {
    "plex": PlexToConfig(service="plex"),
    "lastfm": LastFMToConfig(service="lastfm"),
    "listenbrainz": ListenBrainzToConfig(service="listenbrainz"),
}


def _reset_result(service: str, ok: int, err: int) -> dict:
    return {
        "tracks_fetched": ok + err,
        "results": [{"service": service, "ok": ok, "err": err, "skipped": 0}],
    }


async def _reset_recorded(
    db: AsyncSession,
    target_svc: str,
    on_fetch_complete: Optional[Callable[[int], Awaitable[None]]],
    *,
    on_track: TrackCallback,
    dry_run: bool,
) -> dict:
    """Undo what this tool wrote to one target, leaving everything else alone."""
    store = SqlSyncStore(db)
    records = await store.records_for_target(target_svc)

    if on_fetch_complete is not None:
        await on_fetch_complete(len(records))

    try:
        target = await adapters.target_for(db, _RESET_TARGET_CONFIGS[target_svc])
    except ValueError as exc:
        for _, record in records:
            await on_track(
                adapters.track_to_dict(record.track), target_svc, False, str(exc), False
            )
        return _reset_result(target_svc, 0, len(records))

    ok = err = 0
    for source_service, record in records:
        data = adapters.track_to_dict(record.track)
        reverted = TrackOutcome(
            record.track, target_svc, OutcomeStatus.REVERTED, Reason.WITHDRAWN
        )
        try:
            target.undoable(record)
        except ValueError as exc:
            # The record does not say what we wrote, so clearing anything could
            # remove a rating the user made. Leave it, and say why.
            await on_track(
                data,
                target_svc,
                False,
                str(exc),
                False,
                TrackOutcome(
                    record.track,
                    target_svc,
                    OutcomeStatus.SKIPPED,
                    Reason.UNRECORDED_TARGET,
                    str(exc),
                ),
            )
            err += 1
            continue
        if dry_run:
            await on_track(data, target_svc, True, None, True, reverted)
            ok += 1
            continue
        try:
            await attempt(
                lambda: target.undo(record), f"{target_svc} undo for '{record.key}'"
            )
        except Exception as exc:  # noqa: BLE001 - reported per track
            logger.error("Reset %s failed for '%s': %s", target_svc, record.key, exc)
            await on_track(
                data,
                target_svc,
                False,
                str(exc),
                False,
                TrackOutcome(
                    record.track, target_svc, OutcomeStatus.FAILED, Reason.WRITE_FAILED, str(exc)
                ),
            )
            err += 1
            continue
        await store.forget(source_service, target_svc, record.key)
        await on_track(data, target_svc, True, None, False, reverted)
        ok += 1
    return _reset_result(target_svc, ok, err)


async def _reset_service(
    db: AsyncSession,
    target_svc: str,
    on_fetch_complete: Optional[Callable[[int], Awaitable[None]]],
    *,
    on_track: TrackCallback,
) -> dict:
    """Whole-service wipe: clear every rating, ours or not. Only on request."""
    if target_svc == "plex":
        ok, err = await reset_plex(db, on_fetch_complete, on_track)
    elif target_svc == "lastfm":
        ok, err = await reset_lastfm(db, on_fetch_complete, on_track)
    else:
        ok, err = await reset_listenbrainz(db, on_fetch_complete, on_track)

    # Nothing on the target is ours any more, so the record of our writes goes.
    await SqlSyncStore(db).forget_target(target_svc)
    return _reset_result(target_svc, ok, err)


# The three functions below are the whole-service wipe behind ``_reset_service``.


async def reset_plex(
    db: AsyncSession,
    on_fetch_complete: Optional[Callable[[int], Awaitable[None]]] = None,
    on_track: TrackCallback = _noop_track,
) -> tuple[int, int]:
    library = PlexLibrary(await plex_credentials(db))
    rated = await library.rated(0.0001, "gte")

    if on_fetch_complete is not None:
        await on_fetch_complete(len(rated))

    ok = err = 0
    for plex_track in rated:
        track = {"artist": plex_track.grandparentTitle or "", "title": plex_track.title}
        try:
            await library.rate(str(plex_track.ratingKey), None)
            await on_track(track, "plex", True, None, False)
            ok += 1
        except Exception as exc:  # noqa: BLE001 - reported per track
            logger.error("Plex reset failed for '%s': %s", plex_track.title, exc)
            await on_track(track, "plex", False, str(exc), False)
            err += 1
    return ok, err


async def reset_lastfm(
    db: AsyncSession,
    on_fetch_complete: Optional[Callable[[int], Awaitable[None]]] = None,
    on_track: TrackCallback = _noop_track,
) -> tuple[int, int]:
    client = LastFmClient(await lastfm_credentials(db))
    tracks = await client.loved()

    if on_fetch_complete is not None:
        await on_fetch_complete(len(tracks))

    ok = err = 0
    for track in tracks:
        data = adapters.track_to_dict(track)
        try:
            await client.unlove(track.artist, track.title)
            await on_track(data, "lastfm", True, None, False)
            ok += 1
        except Exception as exc:  # noqa: BLE001 - reported per track
            logger.error("LFM unlove failed '%s – %s': %s", track.artist, track.title, exc)
            await on_track(data, "lastfm", False, str(exc), False)
            err += 1
    return ok, err


async def reset_listenbrainz(
    db: AsyncSession,
    on_fetch_complete: Optional[Callable[[int], Awaitable[None]]] = None,
    on_track: TrackCallback = _noop_track,
) -> tuple[int, int]:
    client = ListenBrainzClient(await listenbrainz_credentials(db))
    feedback = [track for score in (1, -1) for track in await client.feedback(score)]

    if on_fetch_complete is not None:
        await on_fetch_complete(len(feedback))

    ok = err = 0
    for track in feedback:
        data = adapters.track_to_dict(track)
        if not track.recording_mbid:
            await on_track(data, "listenbrainz", False, "No recording MBID", False)
            err += 1
            continue
        try:
            await client.submit(0, track.recording_mbid)
            await on_track(data, "listenbrainz", True, None, False)
            ok += 1
        except Exception as exc:  # noqa: BLE001 - reported per track
            logger.error("LBZ reset failed for %s: %s", track.recording_mbid, exc)
            await on_track(data, "listenbrainz", False, str(exc), False)
            err += 1
    return ok, err


# ---------------------------------------------------------------------------
# High-level execute_relay
# ---------------------------------------------------------------------------


async def execute_relay(
    db: AsyncSession,
    source: object,
    targets: list,
    on_fetch_complete: Optional[Callable[[int], Awaitable[None]]] = None,
    on_track: TrackCallback = _noop_track,
    dry_run: bool = False,
) -> dict:
    """
    Fetch tracks from source and relay to all targets.

    Returns {"tracks_fetched": N,
             "results": [{"service": ..., "ok": N, "err": N, "skipped": N}, ...]}
    """
    if isinstance(source, ResetFromConfig):
        return await _execute_reset(db, source, on_fetch_complete, on_track, dry_run)

    engine_source = await adapters.source_for(db, source)
    source_service: str = engine_source.service
    tracks = await engine_source.fetch()
    logger.info("Relay: fetched %d tracks from %s", len(tracks), source_service)

    progress: ProgressSink = CallbackProgress(on_fetch_complete, on_track, dry_run)
    await progress.fetch_complete(len(tracks))

    ready, unconfigured = [], []
    for config in targets:
        try:
            ready.append(await adapters.target_for(db, config))
        except ValueError as exc:
            unconfigured.append((target_service(config), str(exc)))

    sync_store, _ = adapters.stores(db)
    report = await pipeline.run_tracks(
        tracks,
        ready,
        sync_store=sync_store,
        source_service=source_service,
        progress=progress,
        dry_run=dry_run,
    )

    counts = {result.target: result for result in report.targets}
    results = []
    for config in targets:
        service = target_service(config)
        if service in counts:
            result = counts[service]
            ok = result.written + result.reverted
            err, skipped = result.unmatched + result.failed, result.skipped
        else:
            detail = next(message for name, message in unconfigured if name == service)
            for track in tracks:
                await on_track(
                    adapters.track_to_dict(track), service, False, detail, False, None
                )
            ok, err, skipped = 0, len(tracks), 0
        results.append({"service": service, "ok": ok, "err": err, "skipped": skipped})

    return {"tracks_fetched": len(tracks), "results": results}


async def _execute_reset(
    db: AsyncSession,
    source: ResetFromConfig,
    on_fetch_complete: Optional[Callable[[int], Awaitable[None]]],
    on_track: TrackCallback,
    dry_run: bool = False,
) -> dict:
    target_svc = source.target_service
    mode = "wipe" if source.all else "undo"
    logger.info("Relay: reset (%s) for %s", mode, target_svc)

    if source.all:
        return await _reset_service(
            db, target_svc, on_fetch_complete, on_track=on_track
        )
    return await _reset_recorded(
        db, target_svc, on_fetch_complete, on_track=on_track, dry_run=dry_run
    )
