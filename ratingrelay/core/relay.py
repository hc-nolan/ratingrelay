"""
Core relay logic — fetch from a source service and write to target services.

Extracted from routes/relay.py so it can be called from the background worker
without going through FastAPI's dependency injection.
"""

import asyncio
import re
from functools import partial
from typing import Awaitable, Callable, Optional

import musicbrainzngs as mbz
import pylast
from liblistenbrainz import ListenBrainz
from plexapi.server import PlexServer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

mbz.set_useragent("RatingRelay", "2.0", "https://github.com/hnolan/ratingrelay2")

from ratingrelay.core.thread import run_sync
from ratingrelay.db.credentials import get_credential
from ratingrelay.models.mappings import TrackMapping
from ratingrelay.models.services import ServiceName
from ratingrelay.models.sync import SyncRecord
from ratingrelay.schemas.relay import (
    LastFMFromConfig,
    LastFMToConfig,
    ListenBrainzFromConfig,
    ListenBrainzToConfig,
    PlexFromConfig,
    PlexToConfig,
    ResetFromConfig,
)
from ratingrelay.settings import get_settings, logger

settings = get_settings()

# Callback type: (track_dict, target_service_name, success, error_or_None, skipped)
# skipped=True means the track was already synced; success/error are ignored in that case.
TrackCallback = Callable[[dict, str, bool, Optional[str], bool], Awaitable[None]]


async def _noop_fetch(count: int) -> None:
    pass


async def _noop_track(
    track: dict, target: str, success: bool, error: Optional[str], skipped: bool = False
) -> None:
    pass


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()


def _sync_key(track: dict) -> str:
    return f"{_normalize(track.get('artist', ''))}||{_normalize(track.get('title', ''))}"


async def _load_synced(db: AsyncSession, source_service: str, target_service: str) -> set[str]:
    result = await db.execute(
        select(SyncRecord.normalized_key)
        .where(SyncRecord.source_service == source_service)
        .where(SyncRecord.target_service == target_service)
    )
    return set(result.scalars().all())


async def _record_sync(
    db: AsyncSession, source_service: str, target_service: str, track: dict, recording_mbid: Optional[str] = None
) -> None:
    db.add(SyncRecord(
        source_service=source_service,
        target_service=target_service,
        normalized_key=_sync_key(track),
        recording_mbid=recording_mbid,
    ))
    await db.commit()


async def _load_mappings(
    db: AsyncSession, target_service: str
) -> dict[str, tuple[str, str, Optional[str]]]:
    """Return {normalized_key: (mapped_artist, mapped_title, recording_mbid)} for a target."""
    result = await db.execute(
        select(TrackMapping).where(
            (TrackMapping.target_service == target_service)
            | (TrackMapping.target_service == "all")
        )
    )
    out: dict[str, tuple[str, str, Optional[str]]] = {}
    for m in result.scalars().all():
        # Prefer service-specific mapping over "all"
        if m.source_normalized_key not in out or m.target_service == target_service:
            out[m.source_normalized_key] = (m.mapped_artist, m.mapped_title, m.recording_mbid)
    return out


def _lookup_recording_mbid(
    track_mbid: Optional[str], artist: str, title: str
) -> Optional[str]:
    """Resolve a MusicBrainz recording MBID from a Plex track MBID or artist+title."""
    try:
        if track_mbid:
            result = mbz.search_recordings(query=f"tid:{track_mbid}", limit=1)
            recs = result.get("recording-list", [])
            if recs:
                return recs[0].get("id")
        # Fallback: search by title + artist
        result = mbz.search_recordings(recording=title, artist=artist, limit=5)
        recs = result.get("recording-list", [])
        if not recs:
            return None
        tl, al = title.lower(), artist.lower()
        for rec in recs:
            credits = rec.get("artist-credit", [])
            if rec.get("title", "").lower() == tl and credits and credits[0].get("name", "").lower() == al:
                return rec.get("id")
        return recs[0].get("id")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


async def _resolve_plex_creds(db: AsyncSession) -> tuple[str, str]:
    cred = await get_credential(db, ServiceName.plex)
    token = settings.plex_token or (cred.token if cred else None)
    server_url = (
        str(settings.plex_server_url).rstrip("/")
        if settings.plex_server_url
        else (cred.plex_server_url if cred else None)
    )
    if not token or not server_url:
        raise ValueError("Plex not configured")
    return token, server_url


async def _resolve_lastfm_network(db: AsyncSession) -> "pylast.LastFMNetwork":
    cred = await get_credential(db, ServiceName.lastfm)
    api_key = settings.lastfm_token or (cred.token if cred else None)
    api_secret = settings.lastfm_secret or (cred.secret if cred else None)
    username = settings.lastfm_username or (cred.username if cred else None)
    password_hash = (
        pylast.md5(settings.lastfm_password) if settings.lastfm_password else None
    ) or (cred.password_hash if cred else None)
    if not api_key or not api_secret or not username or not password_hash:
        raise ValueError("Last.fm not configured")
    return await run_sync(
        partial(
            pylast.LastFMNetwork,
            api_key=api_key,
            api_secret=api_secret,
            username=username,
            password_hash=password_hash,
        )
    )


async def _resolve_lbz_creds(db: AsyncSession) -> tuple[str, str]:
    cred = await get_credential(db, ServiceName.listenbrainz)
    token = settings.listenbrainz_token or (cred.token if cred else None)
    username = settings.listenbrainz_username or (cred.username if cred else None)
    if not token or not username:
        raise ValueError("ListenBrainz not configured")
    return token, username


async def _fetch_lbz_feedback(client: ListenBrainz, username: str, score: int) -> list[dict]:
    PAGE_SIZE = 100
    all_feedback: list[dict] = []
    offset = 0
    while True:
        resp = await run_sync(
            partial(client.get_user_feedback, username, score, True, PAGE_SIZE, offset)
        )
        if resp is None:
            break
        batch = resp.get("feedback", [])
        all_feedback.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_feedback


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------


async def fetch_plex(db: AsyncSession, config: PlexFromConfig) -> list[dict]:
    token, server_url = await _resolve_plex_creds(db)

    server = await run_sync(lambda: PlexServer(server_url, token))
    section = await run_sync(
        lambda: server.library.section(settings.plex_music_library)
    )
    all_tracks = await run_sync(lambda: section.searchTracks())

    threshold = config.rating_threshold
    if config.comparison == "gte":
        matched = [t for t in all_tracks if (t.userRating or 0) >= threshold]
    else:
        matched = [t for t in all_tracks if 0 < (t.userRating or 0) <= threshold]

    result = []
    for t in matched:
        artist_name = t.grandparentTitle or ""
        track_mbid = None
        for guid in t.guids:
            if guid.id.startswith("mbid://"):
                track_mbid = guid.id.removeprefix("mbid://")
                break
        result.append(
            {
                "artist": artist_name,
                "title": t.title,
                "plex_rating": t.userRating or 0,
                "track_mbid": track_mbid,
            }
        )
    return result


async def fetch_lastfm(db: AsyncSession, config: LastFMFromConfig) -> list[dict]:
    network = await _resolve_lastfm_network(db)
    user = await run_sync(network.get_authenticated_user)
    loved = await run_sync(lambda: user.get_loved_tracks(limit=None))

    return [
        {"artist": str(item.track.artist), "title": str(item.track.title)}
        for item in loved
    ]


async def fetch_listenbrainz(
    db: AsyncSession, config: ListenBrainzFromConfig
) -> list[dict]:
    token, username = await _resolve_lbz_creds(db)
    score = 1 if config.feedback_type == "love" else -1
    client = ListenBrainz()
    client.set_auth_token(token)
    all_feedback = await _fetch_lbz_feedback(client, username, score)

    return [
        {
            "artist": f.get("track_metadata", {}).get("artist_name", ""),
            "title": f.get("track_metadata", {}).get("track_name", ""),
            "recording_mbid": f.get("recording_mbid", ""),
        }
        for f in all_feedback
    ]


# ---------------------------------------------------------------------------
# Target writers — return (ok_count, err_count)
# ---------------------------------------------------------------------------


async def write_to_plex(
    db: AsyncSession,
    tracks: list[dict],
    config: PlexToConfig,
    source_service: str = "unknown",
    on_track: TrackCallback = _noop_track,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    try:
        token, server_url = await _resolve_plex_creds(db)
    except ValueError:
        for track in tracks:
            await on_track(track, "plex", False, "Plex not configured", False)
        return 0, len(tracks), 0

    already_synced = await _load_synced(db, source_service, "plex")
    mappings = await _load_mappings(db, "plex")

    server = await run_sync(lambda: PlexServer(server_url, token))
    section = await run_sync(
        lambda: server.library.section(settings.plex_music_library)
    )
    all_plex = await run_sync(lambda: section.searchTracks())

    lookup: dict[tuple[str, str], list] = {}
    for pt in all_plex:
        artist = pt.grandparentTitle or ""
        key = (_normalize(artist), _normalize(pt.title))
        lookup.setdefault(key, []).append(pt)

    rating = config.rating
    ok = err = skipped = 0
    for track in tracks:
        norm_key = _sync_key(track)
        if norm_key in already_synced:
            skipped += 1
            if dry_run:
                await on_track(track, "plex", False, None, True)
            continue
        if norm_key in mappings:
            search_artist, search_title = mappings[norm_key][0], mappings[norm_key][1]
        else:
            search_artist = track.get("artist", "")
            search_title = track.get("title", "")
        key = (_normalize(search_artist), _normalize(search_title))
        plex_matches = lookup.get(key, [])
        if not plex_matches:
            await on_track(track, "plex", False, "No match found in Plex library", False)
            err += 1
            continue
        for pt in plex_matches:
            if dry_run:
                await on_track(track, "plex", True, None, False)
                already_synced.add(norm_key)
                ok += 1
            else:
                try:
                    await run_sync(partial(pt.rate, rating))
                    await on_track(track, "plex", True, None, False)
                    await _record_sync(db, source_service, "plex", track)
                    already_synced.add(norm_key)
                    ok += 1
                except Exception as exc:
                    logger.error("Failed to rate Plex track '%s': %s", pt.title, exc)
                    await on_track(track, "plex", False, str(exc), False)
                    err += 1

    return ok, err, skipped


async def write_to_lastfm(
    db: AsyncSession,
    tracks: list[dict],
    config: LastFMToConfig,
    source_service: str = "unknown",
    on_track: TrackCallback = _noop_track,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    try:
        network = await _resolve_lastfm_network(db)
    except ValueError:
        for track in tracks:
            await on_track(track, "lastfm", False, "Last.fm not configured", False)
        return 0, len(tracks), 0

    already_synced = await _load_synced(db, source_service, "lastfm")
    mappings = await _load_mappings(db, "lastfm")

    ok = err = skipped = 0
    for track in tracks:
        norm_key = _sync_key(track)
        if norm_key in already_synced:
            skipped += 1
            if dry_run:
                await on_track(track, "lastfm", False, None, True)
            continue
        if norm_key in mappings:
            artist, title = mappings[norm_key][0], mappings[norm_key][1]
        else:
            artist = track.get("artist", "")
            title = track.get("title", "")
        if not artist or not title:
            await on_track(track, "lastfm", False, "Missing artist or title", False)
            err += 1
            continue
        if dry_run:
            await on_track(track, "lastfm", True, None, False)
            already_synced.add(norm_key)
            ok += 1
        else:
            try:
                lfm_track = pylast.Track(artist, title, network)
                await run_sync(lfm_track.love)
                await asyncio.sleep(0.3)
                await on_track(track, "lastfm", True, None, False)
                await _record_sync(db, source_service, "lastfm", track)
                already_synced.add(norm_key)
                ok += 1
            except Exception as exc:
                logger.error("LFM love failed '%s – %s': %s", artist, title, exc)
                await on_track(track, "lastfm", False, str(exc), False)
                err += 1

    return ok, err, skipped


async def write_to_listenbrainz(
    db: AsyncSession,
    tracks: list[dict],
    config: ListenBrainzToConfig,
    source_service: str = "unknown",
    on_track: TrackCallback = _noop_track,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    cred = await get_credential(db, ServiceName.listenbrainz)
    token = settings.listenbrainz_token or (cred.token if cred else None)
    if not token:
        for track in tracks:
            await on_track(track, "listenbrainz", False, "ListenBrainz not configured", False)
        return 0, len(tracks), 0

    already_synced = await _load_synced(db, source_service, "listenbrainz")
    mappings = await _load_mappings(db, "listenbrainz")

    feedback_score = 1 if config.feedback_type == "love" else -1
    client = ListenBrainz()
    client.set_auth_token(token)

    ok = err = skipped = 0
    for track in tracks:
        norm_key = _sync_key(track)
        if norm_key in already_synced:
            skipped += 1
            if dry_run:
                await on_track(track, "listenbrainz", False, None, True)
            continue
        mapping = mappings.get(norm_key)
        mbid = track.get("recording_mbid", "")
        if not mbid:
            if mapping and mapping[2]:
                mbid = mapping[2]
            else:
                track_mbid = track.get("track_mbid")
                artist = mapping[0] if mapping else track.get("artist", "")
                title = mapping[1] if mapping else track.get("title", "")
                mbid = (
                    await run_sync(partial(_lookup_recording_mbid, track_mbid, artist, title))
                ) or ""
        if not mbid:
            await on_track(track, "listenbrainz", False, "No MusicBrainz recording ID found", False)
            err += 1
            continue
        if dry_run:
            await on_track(track, "listenbrainz", True, None, False)
            already_synced.add(norm_key)
            ok += 1
        else:
            try:
                await _lbz_submit(client, feedback_score, mbid)
                await on_track(track, "listenbrainz", True, None, False)
                await _record_sync(db, source_service, "listenbrainz", track, recording_mbid=mbid)
                already_synced.add(norm_key)
                ok += 1
            except Exception as exc:
                logger.error("LBZ feedback failed for %s: %s", mbid, exc)
                await on_track(track, "listenbrainz", False, str(exc), False)
                err += 1

    return ok, err, skipped


# ---------------------------------------------------------------------------
# ListenBrainz submit helper with 429 retry
# ---------------------------------------------------------------------------


async def _lbz_submit(client: "ListenBrainz", score: int, mbid: str) -> None:
    try:
        await run_sync(partial(client.submit_user_feedback, score, mbid))
    except Exception as exc:
        if "429" in str(exc):
            logger.warning("ListenBrainz rate limited, backing off 60s")
            await asyncio.sleep(60)
            await run_sync(partial(client.submit_user_feedback, score, mbid))
        else:
            raise


# ---------------------------------------------------------------------------
# Reset functions — wipe all ratings from a single service
# ---------------------------------------------------------------------------


async def reset_plex(
    db: AsyncSession,
    on_fetch_complete: Optional[Callable[[int], Awaitable[None]]] = None,
    on_track: TrackCallback = _noop_track,
) -> tuple[int, int]:
    token, server_url = await _resolve_plex_creds(db)
    server = await run_sync(lambda: PlexServer(server_url, token))
    section = await run_sync(lambda: server.library.section(settings.plex_music_library))
    all_tracks = await run_sync(lambda: section.searchTracks())
    rated = [t for t in all_tracks if (t.userRating or 0) > 0]

    if on_fetch_complete is not None:
        await on_fetch_complete(len(rated))

    ok = err = 0
    for t in rated:
        track = {"artist": t.grandparentTitle or "", "title": t.title}
        try:
            await run_sync(partial(t.rate, None))
            await on_track(track, "plex", True, None, False)
            ok += 1
        except Exception as exc:
            logger.error("Plex reset failed for '%s': %s", t.title, exc)
            await on_track(track, "plex", False, str(exc), False)
            err += 1
    return ok, err


async def reset_lastfm(
    db: AsyncSession,
    on_fetch_complete: Optional[Callable[[int], Awaitable[None]]] = None,
    on_track: TrackCallback = _noop_track,
) -> tuple[int, int]:
    network = await _resolve_lastfm_network(db)
    user = await run_sync(network.get_authenticated_user)
    loved = await run_sync(lambda: user.get_loved_tracks(limit=None))
    tracks = [
        {"artist": str(item.track.artist), "title": str(item.track.title)}
        for item in loved
    ]

    if on_fetch_complete is not None:
        await on_fetch_complete(len(tracks))

    ok = err = 0
    for track in tracks:
        artist = track["artist"]
        title = track["title"]
        try:
            lfm_track = pylast.Track(artist, title, network)
            await run_sync(lfm_track.unlove)
            await asyncio.sleep(0.3)
            await on_track(track, "lastfm", True, None, False)
            ok += 1
        except Exception as exc:
            logger.error("LFM unlove failed '%s – %s': %s", artist, title, exc)
            await on_track(track, "lastfm", False, str(exc), False)
            err += 1
    return ok, err


async def reset_listenbrainz(
    db: AsyncSession,
    on_fetch_complete: Optional[Callable[[int], Awaitable[None]]] = None,
    on_track: TrackCallback = _noop_track,
) -> tuple[int, int]:
    token, username = await _resolve_lbz_creds(db)
    client = ListenBrainz()
    client.set_auth_token(token)

    all_feedback: list[dict] = []
    for score in [1, -1]:
        all_feedback.extend(await _fetch_lbz_feedback(client, username, score))

    if on_fetch_complete is not None:
        await on_fetch_complete(len(all_feedback))

    ok = err = 0
    for f in all_feedback:
        mbid = f.get("recording_mbid", "")
        track = {
            "artist": f.get("track_metadata", {}).get("artist_name", ""),
            "title": f.get("track_metadata", {}).get("track_name", ""),
        }
        if not mbid:
            await on_track(track, "listenbrainz", False, "No recording MBID", False)
            err += 1
            continue
        try:
            await _lbz_submit(client, 0, mbid)
            await on_track(track, "listenbrainz", True, None, False)
            ok += 1
        except Exception as exc:
            logger.error("LBZ reset failed for %s: %s", mbid, exc)
            await on_track(track, "listenbrainz", False, str(exc), False)
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

    Returns {"tracks_fetched": N, "results": [{"service": ..., "ok": N, "err": N, "skipped": N}, ...]}
    """
    # Reset mode — wipe all ratings from the target service, no targets needed
    if isinstance(source, ResetFromConfig):
        target_svc = source.target_service
        logger.info("Relay: reset mode for %s", target_svc)
        if target_svc == "plex":
            ok, err = await reset_plex(db, on_fetch_complete, on_track)
        elif target_svc == "lastfm":
            ok, err = await reset_lastfm(db, on_fetch_complete, on_track)
        else:
            ok, err = await reset_listenbrainz(db, on_fetch_complete, on_track)

        # Clear sync records so tracks can be re-synced after reset
        await db.execute(
            delete(SyncRecord).where(SyncRecord.target_service == target_svc)
        )
        await db.commit()
        logger.info("Reset %s: %d cleared, %d errors", target_svc, ok, err)
        return {"tracks_fetched": ok + err, "results": [{"service": target_svc, "ok": ok, "err": err, "skipped": 0}]}

    # Normal relay — fetch from source, write to targets
    if isinstance(source, PlexFromConfig):
        tracks = await fetch_plex(db, source)
    elif isinstance(source, LastFMFromConfig):
        tracks = await fetch_lastfm(db, source)
    elif isinstance(source, ListenBrainzFromConfig):
        tracks = await fetch_listenbrainz(db, source)
    else:
        raise ValueError(f"Unknown source service: {source}")

    source_service: str = source.service  # type: ignore[attr-defined]
    logger.info("Relay: fetched %d tracks from %s", len(tracks), source_service)

    if on_fetch_complete is not None:
        await on_fetch_complete(len(tracks))

    # Write to each target
    results = []
    ok = err = skipped = 0
    for target in targets:
        if isinstance(target, PlexToConfig):
            ok, err, skipped = await write_to_plex(db, tracks, target, source_service, on_track, dry_run)
            results.append({"service": "plex", "ok": ok, "err": err, "skipped": skipped})
        elif isinstance(target, LastFMToConfig):
            ok, err, skipped = await write_to_lastfm(db, tracks, target, source_service, on_track, dry_run)
            results.append({"service": "lastfm", "ok": ok, "err": err, "skipped": skipped})
        elif isinstance(target, ListenBrainzToConfig):
            ok, err, skipped = await write_to_listenbrainz(db, tracks, target, source_service, on_track, dry_run)
            results.append({"service": "listenbrainz", "ok": ok, "err": err, "skipped": skipped})
        else:
            logger.warning("Unknown target type: %s", type(target))
            continue

        logger.info(
            "Relay → %s: %d ok, %d errors, %d skipped",
            results[-1]["service"],
            ok, err, skipped,
        )

    return {"tracks_fetched": len(tracks), "results": results}
