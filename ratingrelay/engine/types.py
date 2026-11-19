"""Value types shared by the engine, the API adapters and (later) the CLI.

Nothing here imports a framework, an ORM or a service client: these are plain
frozen dataclasses and enums so that ``engine`` can stay headless and the
adapters above it can translate them into their own representations.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional

from ratingrelay.engine.normalize import track_key

# The Plex ratings, out of 10, that count as a love (at least) and a hate (at
# most). The relay reads them from settings (``LOVE_THRESHOLD``/``HATE_THRESHOLD``,
# whose defaults match these). One number serves both directions of a pair: a Plex
# source reads loves at the threshold and a Plex target writes the threshold, so
# a two-way setup writes back exactly what it reads and never bumps a track the
# user rated at the threshold up to 5 stars.
DEFAULT_LOVE_RATING = 8.0
DEFAULT_HATE_RATING = 2.0

# A relay reads one of three directions. ``love`` and ``hate`` classify a track by
# the thresholds above and write the threshold; ``rating`` carries the track's own
# value, which is what two services that both have a star scale can do (RR-20).
Mode = Literal["love", "hate", "rating"]

# The smallest rating each service holds, which is the floor a ``rating`` relay reads
# at: "rated at all". Plex's is half a star out of five (1 of 10); hyperfix maps a 0
# to 0.1 on this scale, so its own floor is the same statement.
PLEX_RATING_FLOOR = 1.0
HYPERFIX_RATING_FLOOR = 0.1


@dataclass(frozen=True, slots=True)
class Track:
    """A track as the engine sees it, independent of which service produced it."""

    artist: str
    title: str
    recording_mbid: Optional[str] = None
    track_mbid: Optional[str] = None
    rating: Optional[float] = None
    external_id: Optional[str] = None
    # The recording a previous run resolved this track to. A hint, not a fact:
    # it ranks below what the source says and below a user's mapping, and it is
    # not part of the track's identity.
    known_recording_mbid: Optional[str] = field(default=None, compare=False, repr=False)

    @property
    def key(self) -> str:
        return track_key(self.artist, self.title)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A track that exists on a target service and could be written to."""

    artist: str
    title: str
    recording_mbid: Optional[str] = None
    external_id: Optional[str] = None
    rating: Optional[float] = None


class IntentKind(str, Enum):
    """The state we want a target to hold for a track."""

    LOVE = "love"
    HATE = "hate"
    RATING = "rating"


@dataclass(frozen=True, slots=True)
class Intent:
    """A desired target state, and the key it is stored under.

    ``Intent`` replaces v2's boolean "already synced" flag: storing the intent
    per record is what lets a run notice that a rating changed or a love was
    withdrawn. Two intents of different kinds never compare equal.
    """

    kind: IntentKind
    rating: Optional[float] = None

    def __post_init__(self) -> None:
        if self.kind is IntentKind.RATING and self.rating is None:
            raise ValueError("a rating intent needs a rating value")
        if self.kind is not IntentKind.RATING and self.rating is not None:
            raise ValueError(f"intent {self.kind.value!r} takes no rating value")

    @property
    def key(self) -> str:
        """Stable storage key, e.g. ``love``, ``hate`` or ``rating:8.0``."""
        if self.kind is IntentKind.RATING:
            return f"{self.kind.value}:{self.rating}"
        return self.kind.value


class OutcomeStatus(str, Enum):
    """What happened to one track on one target."""

    WRITTEN = "written"
    SKIPPED = "skipped"
    UNMATCHED = "unmatched"
    FAILED = "failed"
    REVERTED = "reverted"


class Reason(str, Enum):
    """Typed cause for a non-written outcome.

    These replace error strings: presentation must not depend on log wording.
    """

    ALREADY_SYNCED = "already_synced"
    NO_MATCH = "no_match"
    AMBIGUOUS_MATCH = "ambiguous_match"
    NO_RECORDING_MBID = "no_recording_mbid"
    MISSING_METADATA = "missing_metadata"
    NOT_CONFIGURED = "not_configured"
    WRITE_FAILED = "write_failed"
    WITHDRAWN = "withdrawn"
    UNRECORDED_TARGET = "unrecorded_target"
    # The target already held this before we wrote anything; it is the user's,
    # so it is neither written nor recorded (and so never undone).
    ALREADY_ON_TARGET = "already_on_target"
    MASS_UNDO_REFUSED = "mass_undo_refused"
    # The target had dropped a write the record says we made, and this run put
    # it back. Only a relay that asked for it (``reapply_target_drift``) can
    # report this; every other run leaves the target's own edits alone.
    TARGET_DRIFT = "target_drift"
    # The target understood the request and would not do it — it does not hold
    # this track and cannot create it. Distinct from ``write_failed``, which is
    # a transport or server problem that a later run may survive.
    TARGET_REFUSED = "target_refused"


class TargetRefused(Exception):
    """A target said no to this write, and the answer will not change on a retry.

    Raised by a service adapter when the target answered with a refusal (a 404
    for a record it does not hold, a 4xx for a request it rejects) rather than
    with trouble reaching it. The pipeline turns it into
    :attr:`Reason.TARGET_REFUSED` and never retries it: retrying a decision is
    how a run burns its deadline on an answer that was final.
    """


@dataclass(frozen=True, slots=True)
class TrackOutcome:
    """The typed result of processing one track against one target."""

    track: Track
    target: str
    status: OutcomeStatus
    reason: Optional[Reason] = None
    detail: Optional[str] = None
    matches: tuple[Candidate, ...] = ()
    intent: Optional[Intent] = None
    target_key: Optional[str] = None


@dataclass(frozen=True, slots=True)
class TargetReport:
    """Per-target totals, derived from outcomes so the counts cannot drift."""

    target: str
    written: int = 0
    skipped: int = 0
    unmatched: int = 0
    failed: int = 0
    reverted: int = 0

    @classmethod
    def from_outcomes(
        cls, target: str, outcomes: tuple[TrackOutcome, ...]
    ) -> "TargetReport":
        counted = dict.fromkeys(
            (
                OutcomeStatus.WRITTEN,
                OutcomeStatus.SKIPPED,
                OutcomeStatus.UNMATCHED,
                OutcomeStatus.REVERTED,
            ),
            0,
        )
        failed = 0
        for outcome in outcomes:
            if outcome.target != target:
                continue
            if outcome.status is OutcomeStatus.FAILED:
                failed += 1
            else:
                counted[outcome.status] += 1
        return cls(
            target=target,
            written=counted[OutcomeStatus.WRITTEN],
            skipped=counted[OutcomeStatus.SKIPPED],
            unmatched=counted[OutcomeStatus.UNMATCHED],
            failed=failed,
            reverted=counted[OutcomeStatus.REVERTED],
        )


@dataclass(frozen=True, slots=True)
class RunReport:
    """The result of one relay run, in a shape a CLI can print and an API can serve."""

    source: str
    tracks_fetched: int
    targets: tuple[TargetReport, ...]
    outcomes: tuple[TrackOutcome, ...] = ()
    dry_run: bool = False

    @property
    def had_errors(self) -> bool:
        return any(report.failed for report in self.targets)

    @property
    def status(self) -> str:
        """``completed``, ``partial`` or ``failed``, matching the job statuses."""
        if not self.targets:
            return "failed"
        # Only a run that converged nothing failed outright. One that wrote,
        # reverted or found tracks already in sync got through, and its errors
        # make it partial: exit 2 means "could not complete", not "one write
        # failed on a night with nothing new".
        converged = sum(
            report.written + report.reverted + report.skipped for report in self.targets
        )
        if converged == 0 and self.had_errors:
            return "failed"
        return "partial" if self.had_errors else "completed"
