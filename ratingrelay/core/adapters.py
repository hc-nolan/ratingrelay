"""Wire the engine to this application: request configs in, ports out.

This is the seam AGENTS.md asks for — ``api``/``core`` marshal, the engine
decides. Everything here is translation: a pydantic config becomes a source or
target, a dict becomes a :class:`~ratingrelay.engine.types.Track`, and the
database becomes the engine's two stores.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.factory import (  # re-exported for callers of adapters.stores
    SOURCE_SERVICES,
    TARGET_SERVICES,
    mbid_cache,
    source_for_service,
    stores,
    target_for_service,
)
from ratingrelay.engine.services import Source, Target
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


async def source_for(db: AsyncSession, config: object) -> Source:
    """Build the source described by a request config."""
    if isinstance(config, PlexFromConfig):
        return await source_for_service(
            db,
            "plex",
            rating_threshold=config.rating_threshold,
            comparison=config.comparison,
        )
    if isinstance(config, LastFMFromConfig):
        return await source_for_service(db, "lastfm")
    if isinstance(config, ListenBrainzFromConfig):
        return await source_for_service(
            db, "listenbrainz", feedback_type=config.feedback_type
        )
    raise ValueError(f"Unknown source service: {config}")


async def target_for(db: AsyncSession, config: object) -> Target:
    """Build the target described by a request config."""
    if isinstance(config, PlexToConfig):
        return await target_for_service(db, "plex", plex_rating=config.rating)
    if isinstance(config, LastFMToConfig):
        return await target_for_service(db, "lastfm")
    if isinstance(config, ListenBrainzToConfig):
        return await target_for_service(
            db, "listenbrainz", feedback_type=config.feedback_type
        )
    raise ValueError(f"Unknown target service: {config}")
