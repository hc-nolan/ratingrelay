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
    hyperfix_credentials,
    lastfm_credentials,
    listenbrainz_credentials,
    plex_credentials,
)
from ratingrelay.db.store import SqlMappingStore, SqlMbidCache, SqlSyncStore
from ratingrelay.engine.services import Source, Target
from ratingrelay.engine.services.hyperfix import (
    HyperfixClient,
    HyperfixSource,
    HyperfixTarget,
)
from ratingrelay.engine.services.lastfm import LastFmClient, LastFmSource, LastFmTarget
from ratingrelay.engine.services.listenbrainz import (
    ListenBrainzClient,
    ListenBrainzSource,
    ListenBrainzTarget,
)
from ratingrelay.engine.services.musicbrainz import resolve_recording_mbid
from ratingrelay.engine.services.plex import PlexLibrary, PlexSource, PlexTarget
from ratingrelay.core.thresholds import effective_thresholds
from ratingrelay.engine.refs import KNOWN_SERVICES, parse_ref, service_kind
from ratingrelay.engine.types import (
    DEFAULT_LOVE_RATING,
    HYPERFIX_RATING_FLOOR,
    PLEX_RATING_FLOOR,
)
from ratingrelay.settings import get_settings

# Kept as the names the CLI and the request schemas import. A *ref* is one of
# these kinds, optionally followed by ``:account`` (see ``engine.refs``).
#
# Every kind is both. Hyperfix is a source and a target (HF-198); the one pairing it
# cannot join is hyperfix -> plex, because hyperfix already writes its own ratings to
# Plex and the two would undo each other (``pairing_refusal``).
SOURCE_SERVICES = KNOWN_SERVICES
TARGET_SERVICES = KNOWN_SERVICES


def relay_identity(service: str, *, feedback_type: str = "love") -> str:
    """Which relay a source config is, for keeping its sync records apart.

    The identity is the direction the source reads — ``love``, ``hate`` or
    ``rating`` — and nothing finer. Two relays on one pair that read opposite
    directions must not reconcile against each other's writes; one relay whose
    threshold moved must, because a track that no longer passes is a withdrawal to
    undo.

    ``rating`` is its own identity on purpose: a star relay and a love relay on the
    same pair classify the same field differently, and sharing records would let one
    undo the other's writes (RR-18).

    The account is deliberately not part of it: a sync record is keyed by the
    pair of refs as well, so two accounts of one kind are already told apart,
    and folding a ref in here would orphan records the day a label changed.
    """
    kind = service_kind(service)
    if kind == "lastfm":
        return "love"
    if kind in ("plex", "listenbrainz", "hyperfix"):
        if feedback_type == "rating":
            return "rating"
        return "love" if feedback_type == "love" else "hate"
    raise ValueError(f"Unknown source service: {service}")


# Targets that can only record a love. Writing a hate relay to one would love the
# hated tracks, so that pairing is refused before anything is fetched.
LOVE_ONLY_TARGETS = ("lastfm",)
# Targets with no star scale at all: a ``rating`` relay would have to invent the
# meaning of a number they cannot hold.
NO_RATING_TARGETS = ("lastfm", "listenbrainz")


def unsupported_target(relay: str, target: str) -> Optional[str]:
    """Why *target* (a ref) cannot receive a run of *relay*, or ``None`` if it can.

    Compared by kind: a hate relay cannot reach any Last.fm account, however the
    account is named.
    """
    if relay == "hate" and service_kind(target) in LOVE_ONLY_TARGETS:
        return f"{target} has no hate to write; a hate relay cannot target it"
    if relay == "rating" and service_kind(target) in NO_RATING_TARGETS:
        return f"{target} has no star rating to write; a rating relay cannot target it"
    return None


def pairing_refusal(source: str, target: str, *, relay: str) -> Optional[str]:
    """Why *source* → *target* cannot be a run of *relay*, or ``None`` if it can.

    The one place a pairing is judged, so the API, the queue and the CLI cannot
    disagree. Read from *refs*, not kinds: ``plex`` → ``plex:attic`` is the whole
    point of a second account, while ``plex`` → ``plex`` is that account
    relaying into itself and is refused.
    """
    if parse_ref(source).ref == parse_ref(target).ref:
        return f"{source} is both the source and a target"
    if service_kind(source) == "hyperfix" and service_kind(target) == "plex":
        # Two reconcilers on one field: hyperfix pushes album and track ratings to Plex
        # itself, and each side treats the other's write as the user's, so they stop
        # converging. Moving Plex writes into the relay is a separate decision.
        return (
            f"{source} already writes Plex ratings; a relay from it cannot target {target}"
        )
    return unsupported_target(relay, target)


async def plex_threshold(db: AsyncSession, relay: str) -> float:
    """The Plex rating that means *relay* (``love``/``hate``).

    One number per direction, shared by both ends of a pair: a Plex source reads
    loves at or above it and a Plex target writes it, so a two-way setup writes
    back exactly what it reads. It comes from settings or the UI
    (:func:`effective_thresholds`); changing it moves every relay at once.
    """
    current = await effective_thresholds(db, get_settings())
    overlap = current.overlap()
    if overlap:
        # Settings pin one side and the UI saved the other past it: fail the
        # run rather than read or write a track as both.
        raise ValueError(overlap)
    return current.for_relay(relay)


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
    min_rating: Optional[float] = None,
) -> Source:
    """Build a source from a ref (``plex``, ``listenbrainz:old``).

    *feedback_type* is the direction to read, for every source that has one:
    Plex loves are rated at least the love threshold, hates at most the hate
    threshold (:func:`plex_threshold`), and a ``rating`` relay reads every rated
    track and carries its own value. Last.fm and ListenBrainz have no star scale,
    so neither can be the source of one.
    """
    kind = service_kind(service)
    rating_mode = feedback_type == "rating"
    if rating_mode and kind in NO_RATING_TARGETS:
        raise ValueError(f"{service} has no star rating to read; a rating relay cannot use it")
    if kind == "plex":
        library = PlexLibrary(await plex_credentials(db, service))
        relay = relay_identity(service, feedback_type=feedback_type)
        if rating_mode:
            # "Rated at all": half a star is Plex's smallest rating, and the server
            # filters on it, so an unrated track never reaches the run.
            return PlexSource(
                library, min_rating or PLEX_RATING_FLOOR, "gte", service=service
            )
        comparison = "gte" if relay == "love" else "lte"
        return PlexSource(
            library, await plex_threshold(db, relay), comparison, service=service
        )
    if kind == "lastfm":
        return LastFmSource(
            LastFmClient(await lastfm_credentials(db, service)), service=service
        )
    if kind == "listenbrainz":
        client = ListenBrainzClient(await listenbrainz_credentials(db, service))
        return ListenBrainzSource(client, _score(feedback_type), service=service)
    if kind == "hyperfix":
        # Same shared thresholds as Plex: hyperfix's 80/20 love/hate *is* 8.0/2.0
        # here, so a relay reads back what hyperfix holds.
        relay = relay_identity(service, feedback_type=feedback_type)
        client = HyperfixClient(await hyperfix_credentials(db, service))
        if rating_mode:
            # hyperfix maps a stored 0 to 0.1 on this scale, so "at least 0.1" is
            # the same statement Plex's half star is.
            return HyperfixSource(
                client, min_rating or HYPERFIX_RATING_FLOOR, "gte", service=service
            )
        comparison = "gte" if relay == "love" else "lte"
        return HyperfixSource(
            client, await plex_threshold(db, relay), comparison, service=service
        )
    raise ValueError(f"Unknown source service: {service}")


async def target_for_service(
    db: AsyncSession,
    service: str,
    *,
    plex_library: Optional[PlexLibrary] = None,
    relay: str = "love",
) -> Target:
    """Build a target from a ref, wired to the mappings store and MBID cache.

    *relay* is the run's :func:`relay_identity`. A Plex target writes that
    direction's threshold (:func:`plex_threshold`), or the track's own rating in
    ``rating`` mode, and needs the direction to know whether a copy already rated
    above (love), below (hate) or at (rating) exactly it holds it.
    A ListenBrainz target writes that direction's feedback: there is no separate
    target direction, so a target can never write the opposite of its source.

    *service* is a ref, so ``plex`` → ``plex:attic`` is built from the attic's
    own credentials and gets its own Plex library and mapping records.
    """
    refusal = unsupported_target(relay, service)
    if refusal:
        # Also here, not only where configs are accepted: a job stored before
        # the pairing was refused must fail, not love what the user hates.
        raise ValueError(refusal)
    kind = service_kind(service)
    _, mappings = stores(db)
    if kind == "plex":
        library = plex_library or PlexLibrary(await plex_credentials(db, service))
        return PlexTarget(
            library,
            mappings,
            DEFAULT_LOVE_RATING if relay == "rating" else await plex_threshold(db, relay),
            relay,
            service=service,
        )
    if kind == "lastfm":
        client = LastFmClient(await lastfm_credentials(db, service))
        return LastFmTarget(client, mappings, service=service)
    if kind == "listenbrainz":
        client = ListenBrainzClient(await listenbrainz_credentials(db, service))
        return ListenBrainzTarget(
            client,
            mappings,
            resolve_recording_mbid,
            _score(relay),
            cache=mbid_cache(db),
            service=service,
        )
    if kind == "hyperfix":
        return HyperfixTarget(
            HyperfixClient(await hyperfix_credentials(db, service)),
            mappings,
            resolve_recording_mbid,
            DEFAULT_LOVE_RATING if relay == "rating" else await plex_threshold(db, relay),
            relay,
            cache=mbid_cache(db),
            service=service,
        )
    raise ValueError(f"Unknown target service: {service}")
