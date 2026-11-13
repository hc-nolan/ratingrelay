"""Build a source or target from a service name and plain values.

This is the layer both entry points share: the API marshals a request config
into these calls, and the CLI passes flags. Keeping the construction here is
what stops the headless path from drifting from the web path on which store,
which mapping resolver and which MBID cache a target is handed — the drift that
would quietly break reconcile rather than raise.

No pydantic, no FastAPI: settings and the credential row are the only inputs.
"""

from typing import Optional

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

SOURCE_SERVICES = ("plex", "lastfm", "listenbrainz")
TARGET_SERVICES = ("plex", "lastfm", "listenbrainz")


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


async def source_for_service(
    db: AsyncSession,
    service: str,
    *,
    rating_threshold: float = 8.0,
    comparison: str = "gte",
    feedback_type: str = "love",
) -> Source:
    """Build a source by name. Addresses only the options that source reads."""
    if service == "plex":
        library = PlexLibrary(await plex_credentials(db))
        return PlexSource(library, rating_threshold, comparison)
    if service == "lastfm":
        return LastFmSource(LastFmClient(await lastfm_credentials(db)))
    if service == "listenbrainz":
        client = ListenBrainzClient(await listenbrainz_credentials(db))
        return ListenBrainzSource(client, _score(feedback_type))
    raise ValueError(f"Unknown source service: {service}")


async def target_for_service(
    db: AsyncSession,
    service: str,
    *,
    plex_rating: float = 10.0,
    feedback_type: str = "love",
    plex_library: Optional[PlexLibrary] = None,
) -> Target:
    """Build a target by name, wired to the mappings store and MBID cache."""
    _, mappings = stores(db)
    if service == "plex":
        library = plex_library or PlexLibrary(await plex_credentials(db))
        return PlexTarget(library, mappings, plex_rating)
    if service == "lastfm":
        client = LastFmClient(await lastfm_credentials(db))
        return LastFmTarget(client, mappings)
    if service == "listenbrainz":
        client = ListenBrainzClient(await listenbrainz_credentials(db))
        return ListenBrainzTarget(
            client,
            mappings,
            resolve_recording_mbid,
            _score(feedback_type),
            cache=mbid_cache(db),
        )
    raise ValueError(f"Unknown target service: {service}")
