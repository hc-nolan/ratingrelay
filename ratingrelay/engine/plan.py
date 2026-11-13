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
        )
