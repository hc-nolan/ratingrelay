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
from ratingrelay.settings import get_settings

SOURCE_SERVICES = ("plex", "lastfm", "listenbrainz")
TARGET_SERVICES = ("plex", "lastfm", "listenbrainz")


def relay_identity(service: str, *, feedback_type: str = "love") -> str:
    """Which relay a source config is, for keeping its sync records apart.

    The identity is the direction the source reads — ``love`` or ``hate`` — and
    nothing finer. Two relays on one pair that read opposite directions must not
    reconcile against each other's writes; one relay whose threshold moved must,
    because a track that no longer passes is a withdrawal to undo.
    """
    if service == "lastfm":
        return "love"
    if service in ("plex", "listenbrainz"):
        return "love" if feedback_type == "love" else "hate"
    raise ValueError(f"Unknown source service: {service}")


def plex_threshold(relay: str) -> float:
    """The Plex rating that means *relay* (``love``/``hate``), from settings.

    One number per direction, shared by both ends of a pair: a Plex source reads
    loves at or above it and a Plex target writes it, so a two-way setup writes
    back exactly what it reads. Changing it in settings moves both at once.
    """
    current = get_settings()
    return current.love_threshold if relay == "love" else current.hate_threshold


def stores(
    db: AsyncSession, relay: Optional[str] = None, *, dry_run: bool = False
) -> tuple[SqlSyncStore, SqlMappingStore]:
    """The engine's two stores, backed by the current session.

    *relay* (from :func:`relay_identity`) scopes the sync store; a run must pass
    it. Only readers that span relays — reset, the queue — leave it out. A dry
    run gets a read-only sync store: a preview must not change what the real
    run will reconcile against.
    """
    return SqlSyncStore(db, relay, read_only=dry_run), SqlMappingStore(db)


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
    feedback_type: str = "love",
) -> Source:
    """Build a source by name.

    *feedback_type* is the direction to read, for every source that has one:
    Plex loves are rated at least the love threshold, hates at most the hate
    threshold (:func:`plex_threshold`).
    """
    if service == "plex":
        library = PlexLibrary(await plex_credentials(db))
        relay = relay_identity("plex", feedback_type=feedback_type)
        comparison = "gte" if relay == "love" else "lte"
        return PlexSource(library, plex_threshold(relay), comparison)
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
    feedback_type: str = "love",
    plex_library: Optional[PlexLibrary] = None,
    relay: str = "love",
) -> Target:
    """Build a target by name, wired to the mappings store and MBID cache.

    *relay* is the run's :func:`relay_identity`. A Plex target writes that
    direction's threshold (:func:`plex_threshold`), and needs the direction to
    know whether a copy already rated above (love) or below (hate) it holds it.
    """
    _, mappings = stores(db)
    if service == "plex":
        library = plex_library or PlexLibrary(await plex_credentials(db))
        return PlexTarget(library, mappings, plex_threshold(relay), relay)
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
