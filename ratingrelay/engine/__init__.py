"""Headless relay engine.

The engine owns the behaviour contract in ``AGENTS.md``: fetch → match →
reconcile → write. It must not import FastAPI, request schemas, request
sessions or SSE plumbing; callers above it (``api/``, ``cli/``) marshal their
own representations into the types here and back out again.

Layout:

``normalize``   key formats, including the on-disk key shared with the database
``types``       tracks, intents, outcomes and run reports
``matching``    the MBID → exact → variant → containment ladder
``plan``        what a target would write, and the closure that writes it
``store``       the ports the pipeline needs (sync records, mappings, progress)
``services``    one module per service: Plex, Last.fm, ListenBrainz, MusicBrainz
``pipeline``    the run itself: skip, match, plan, commit
"""

from ratingrelay.engine.credentials import (
    LastFmCredentials,
    ListenBrainzCredentials,
    PlexCredentials,
)
from ratingrelay.engine.matching import (
    MatchOutcome,
    MatchStatus,
    MatchTier,
    match_track,
)
from ratingrelay.engine.normalize import (
    comparison_form,
    normalize,
    track_key,
    variant_form,
    variant_key,
)
from ratingrelay.engine.pipeline import run, run_tracks
from ratingrelay.engine.plan import WritePlan
from ratingrelay.engine.refs import (
    KNOWN_SERVICES,
    ServiceRef,
    account_ref,
    is_primary,
    mapping_scopes,
    parse_ref,
    service_kind,
)
from ratingrelay.engine.store import (
    MemoryMappingStore,
    MemorySyncStore,
    MappingStore,
    NullProgress,
    ProgressSink,
    SyncedTrack,
    SyncStore,
    TrackMapping,
)
from ratingrelay.engine.types import (
    Candidate,
    Intent,
    IntentKind,
    OutcomeStatus,
    Reason,
    RunReport,
    TargetReport,
    Track,
    TrackOutcome,
)

__all__ = [
    "Candidate",
    "Intent",
    "IntentKind",
    "KNOWN_SERVICES",
    "LastFmCredentials",
    "ListenBrainzCredentials",
    "MappingStore",
    "MatchOutcome",
    "MatchStatus",
    "MatchTier",
    "MemoryMappingStore",
    "MemorySyncStore",
    "NullProgress",
    "OutcomeStatus",
    "PlexCredentials",
    "ProgressSink",
    "Reason",
    "RunReport",
    "ServiceRef",
    "SyncStore",
    "SyncedTrack",
    "TargetReport",
    "Track",
    "TrackMapping",
    "TrackOutcome",
    "WritePlan",
    "account_ref",
    "comparison_form",
    "is_primary",
    "mapping_scopes",
    "match_track",
    "normalize",
    "parse_ref",
    "run",
    "run_tracks",
    "service_kind",
    "track_key",
    "variant_form",
    "variant_key",
]
