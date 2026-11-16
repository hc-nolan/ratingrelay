"""The write boundary: what a target would do, and how to do it.

A target returns a :class:`WritePlan` rather than performing the write itself.
The pipeline is then the only place that decides whether a plan is committed,
which is what makes a dry run and a real run take identical decisions: they run
the same matching code and differ only in whether ``commit`` is called.

This is also where a target reports why it cannot write, in typed form. The
``detail`` text is for display and for the API's compatibility layer; nothing
in the engine branches on it.
"""

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ratingrelay.engine.types import (
    Candidate,
    Intent,
    OutcomeStatus,
    Reason,
    Track,
    TrackOutcome,
)

Commit = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Existing:
    """What the target held for this track before the plan was made."""

    # The target already holds this intent (or better). Without a record of our
    # own it is the user's state: the pipeline skips it and records nothing.
    holds: bool = False
    # What a write would replace, opaque per target like ``target_key``; undo
    # restores it rather than clearing.
    prior: Optional[str] = None
    # Whether the target holds *anything* here: a rating, a score, a love.
    # ``not holds and not occupied`` is a target that holds nothing at all, which
    # is how a run tells a write that was removed from one somebody replaced. A
    # relay with ``reapply_target_drift`` re-writes only the former. ``None`` is
    # "the target did not say", and is read as occupied: a plan that forgets to
    # fill this in must never cause a write on every run.
    occupied: Optional[bool] = None


@dataclass(frozen=True, slots=True)
class WritePlan:
    """One target's decision about one track."""

    track: Track
    target: str
    status: OutcomeStatus = OutcomeStatus.WRITTEN
    reason: Optional[Reason] = None
    detail: Optional[str] = None
    matches: tuple[Candidate, ...] = ()
    recording_mbid: Optional[str] = None
    intent: Optional[Intent] = None
    target_key: Optional[str] = None
    # Whether a user's mapping decided where this goes. Only then may the
    # pipeline move an existing write to a new target_key: a human correction
    # re-points, a matcher's change of mind never does.
    mapped: bool = False
    existing: Existing = Existing()
    commit: Optional[Commit] = field(default=None, repr=False, compare=False)

    @property
    def actionable(self) -> bool:
        """True when the plan is a write waiting to happen."""
        return self.status is OutcomeStatus.WRITTEN and self.commit is not None

    def to_outcome(
        self,
        status: Optional[OutcomeStatus] = None,
        reason: Optional[Reason] = None,
        detail: Optional[str] = None,
    ) -> TrackOutcome:
        """Freeze the plan into a reportable outcome."""
        return TrackOutcome(
            track=self.track,
            target=self.target,
            status=self.status if status is None else status,
            reason=self.reason if reason is None else reason,
            detail=self.detail if detail is None else detail,
            matches=self.matches,
            intent=self.intent,
            target_key=self.target_key,
        )
