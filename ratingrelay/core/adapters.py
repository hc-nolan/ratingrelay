"""Wire the engine to this application: request configs in, ports out.

This is the seam AGENTS.md asks for — ``api``/``core`` marshal, the engine
decides. Everything here is translation: a pydantic config becomes a source or
target, and an engine :class:`~ratingrelay.engine.types.Track` becomes the
dict shape the worker and the SPA read.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.factory import (
    relay_identity,
    source_for_service,
    target_for_service,
)
from ratingrelay.engine.refs import account_ref
from ratingrelay.engine.services import Source, Target
from ratingrelay.engine.types import Track
from ratingrelay.schemas.relay import (
    HyperfixFromConfig,
    HyperfixToConfig,
    LastFMFromConfig,
    LastFMToConfig,
    ListenBrainzFromConfig,
    ListenBrainzToConfig,
    PlexFromConfig,
    PlexToConfig,
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
            db, ref_for(config), feedback_type=config.feedback_type
        )
    if isinstance(config, LastFMFromConfig):
        return await source_for_service(db, ref_for(config))
    if isinstance(config, ListenBrainzFromConfig):
        return await source_for_service(
            db, ref_for(config), feedback_type=config.feedback_type
        )
    if isinstance(config, HyperfixFromConfig):
        return await source_for_service(
            db, ref_for(config), feedback_type=config.feedback_type
        )
    raise ValueError(f"Unknown source service: {config}")


def ref_for(config: object) -> str:
    """The account ref a request config names.

    A config with no ``account`` names the account that already exists — the ref
    that is just its kind — which is why a job stored before accounts existed
    still means what it meant.
    """
    return account_ref(str(getattr(config, "service")), getattr(config, "account", None))


def relay_for(config: object) -> str:
    """The relay identity of a request's source config (see ``relay_identity``)."""
    if isinstance(config, PlexFromConfig):
        return relay_identity(ref_for(config), feedback_type=config.feedback_type)
    if isinstance(config, LastFMFromConfig):
        return relay_identity(ref_for(config))
    if isinstance(config, ListenBrainzFromConfig):
        return relay_identity(ref_for(config), feedback_type=config.feedback_type)
    if isinstance(config, HyperfixFromConfig):
        return relay_identity(ref_for(config), feedback_type=config.feedback_type)
    raise ValueError(f"Unknown source service: {config}")


async def target_for(db: AsyncSession, config: object, relay: str = "love") -> Target:
    """Build the target described by a request config, for a run of *relay*."""
    if isinstance(config, PlexToConfig):
        return await target_for_service(db, ref_for(config), relay=relay)
    if isinstance(config, LastFMToConfig):
        return await target_for_service(db, ref_for(config), relay=relay)
    if isinstance(config, ListenBrainzToConfig):
        return await target_for_service(db, ref_for(config), relay=relay)
    if isinstance(config, HyperfixToConfig):
        return await target_for_service(db, ref_for(config), relay=relay)
    raise ValueError(f"Unknown target service: {config}")
