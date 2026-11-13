"""Headless relay engine.

The engine owns the behaviour contract in ``AGENTS.md``: fetch → match →
reconcile → write. It must not import FastAPI, request schemas, request
sessions or SSE plumbing; callers above it (``api/``, ``cli/``) marshal their
own representations into the types here and back out again.

Current contents are the pure pieces (normalisation, value types, the matching
ladder). Fetching, reconciliation and writing move here in the remaining slices
of ``docs/plans/2026-11-12-002-engine-extraction-and-reconcile.md``.
"""

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
    "MatchOutcome",
    "MatchStatus",
    "MatchTier",
    "OutcomeStatus",
    "Reason",
    "RunReport",
    "TargetReport",
    "Track",
    "TrackOutcome",
    "comparison_form",
    "match_track",
    "normalize",
    "track_key",
    "variant_form",
    "variant_key",
]
