"""Value types shared by the engine, the API adapters and (later) the CLI.

Nothing here imports a framework, an ORM or a service client: these are plain
frozen dataclasses and enums so that ``engine`` can stay headless and the
adapters above it can translate them into their own representations.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ratingrelay.engine.normalize import track_key


@dataclass(frozen=True, slots=True)
class Track:
    """A track as the engine sees it, independent of which service produced it."""

    artist: str
    title: str
    recording_mbid: Optional[str] = None
    track_mbid: Optional[str] = None
    rating: Optional[float] = None
    external_id: Optional[str] = None

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


@dataclass(frozen=True, slots=True)
class TargetReport:
    """Per-target totals, derived from outcomes so the counts cannot drift."""

    target: str
    written: int = 0
    skipped: int = 0
    unmatched: int = 0
    failed: int = 0

    @classmethod
    def from_outcomes(
        cls, target: str, outcomes: tuple[TrackOutcome, ...]
    ) -> "TargetReport":
        counts = dict.fromkeys(
            (OutcomeStatus.WRITTEN, OutcomeStatus.SKIPPED, OutcomeStatus.UNMATCHED),
            0,
        )
        failed = 0
        for outcome in outcomes:
            if outcome.target != target:
                continue
            if outcome.status is OutcomeStatus.FAILED:
                failed += 1
            else:
                counts[outcome.status] += 1
        return cls(
            target=target,
            written=counts[OutcomeStatus.WRITTEN],
            skipped=counts[OutcomeStatus.SKIPPED],
            unmatched=counts[OutcomeStatus.UNMATCHED],
            failed=failed,
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
        written = sum(report.written for report in self.targets)
        if written == 0 and self.had_errors:
            return "failed"
        return "partial" if self.had_errors else "completed"
