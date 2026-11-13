"""Wire the engine to this application: request configs in, ports out.

This is the seam AGENTS.md asks for — ``api``/``core`` marshal, the engine
decides. Everything here is translation: a pydantic config becomes a source or
target, a dict becomes a :class:`~ratingrelay.engine.types.Track`, and the
database becomes the engine's two stores.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.credentials import (
    lastfm_credentials,
    listenbrainz_credentials,
    plex_credentials,
)
from ratingrelay.db.store import SqlMappingStore, SqlMbidCache, SqlSyncStore
from ratingrelay.engine.services import Source, Target
from ratingrelay.engine.services.lastfm import LastFmClient, LastFmSource, LastFmTarget
from ratingrelay.engine.services.listenbrainz import (
    ListenBrainzClient,
    ListenBrainzSource,
    ListenBrainzTarget,
)
from ratingrelay.engine.services.musicbrainz import resolve_recording_mbid
from ratingrelay.engine.services.plex import PlexLibrary, PlexSource, PlexTarget
from ratingrelay.engine.types import Track
from ratingrelay.schemas.relay import (
    LastFMFromConfig,
    LastFMToConfig,
    ListenBrainzFromConfig,
    ListenBrainzToConfig,
    PlexFromConfig,
    PlexToConfig,
)


def track_from_dict(track: dict) -> Track:
    """Build an engine track from the dict shape the API and worker already use."""
    return Track(
        artist=track.get("artist", ""),
        title=track.get("title", ""),
        recording_mbid=track.get("recording_mbid") or None,
        track_mbid=track.get("track_mbid") or None,
        rating=track.get("plex_rating"),
    )


def track_to_dict(track: Track) -> dict:
    """Back to the dict shape the worker's callbacks and the SPA expect."""
    out: dict = {"artist": track.artist, "title": track.title}
    if track.recording_mbid:
        out["recording_mbid"] = track.recording_mbid
    if track.track_mbid:
        out["track_mbid"] = track.track_mbid
    if track.rating is not None:
        out["plex_rating"] = track.rating
    return out


def stores(db: AsyncSession) -> tuple[SqlSyncStore, SqlMappingStore]:
    """The engine's two stores, backed by the current session."""
    return SqlSyncStore(db), SqlMappingStore(db)


def mbid_cache(db: AsyncSession) -> SqlMbidCache:
    """Resolved recording MBIDs, backed by the current session.

    Separate from :func:`stores` because its rows outlive what a reset deletes.
    """
    return SqlMbidCache(db)


def _score(feedback_type: str) -> int:
    return 1 if feedback_type == "love" else -1


async def source_for(db: AsyncSession, config: object) -> Source:
    """Build the source described by a request config."""
    if isinstance(config, PlexFromConfig):
        library = PlexLibrary(await plex_credentials(db))
        return PlexSource(library, config.rating_threshold, config.comparison)
    if isinstance(config, LastFMFromConfig):
        return LastFmSource(LastFmClient(await lastfm_credentials(db)))
    if isinstance(config, ListenBrainzFromConfig):
        client = ListenBrainzClient(await listenbrainz_credentials(db))
        return ListenBrainzSource(client, _score(config.feedback_type))
    raise ValueError(f"Unknown source service: {config}")


async def target_for(db: AsyncSession, config: object) -> Target:
    """Build the target described by a request config."""
    _, mappings = stores(db)
    if isinstance(config, PlexToConfig):
        library = PlexLibrary(await plex_credentials(db))
        return PlexTarget(library, mappings, config.rating)
    if isinstance(config, LastFMToConfig):
        client = LastFmClient(await lastfm_credentials(db))
        return LastFmTarget(client, mappings)
    if isinstance(config, ListenBrainzToConfig):
        client = ListenBrainzClient(await listenbrainz_credentials(db))
        return ListenBrainzTarget(
            client,
            mappings,
            resolve_recording_mbid,
            _score(config.feedback_type),
            cache=mbid_cache(db),
        )
    raise ValueError(f"Unknown target service: {config}")
