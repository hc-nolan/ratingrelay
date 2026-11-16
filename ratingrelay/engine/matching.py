"""The matching ladder: MBID -> exact -> variant -> containment.

The ladder exists because a source track and a target's copy of it disagree on
spelling far more often than on identity: remaster suffixes, accents, quotes,
"feat." credits, "The" prefixes. Each lower rung is looser, so each one is
strict about ambiguity:

* MBID and exact rungs may match several candidates — those are copies of the
  same recording (duplicate files, multiple discs) and writing to all of them is
  correct.
* Variant and containment rungs must leave exactly one candidate. Several means
  a human decides: the outcome is ``AMBIGUOUS`` with the candidates attached and
  nothing is written.

A run may therefore propose a match, but it never silently re-points a track.
"""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Optional, Sequence

from ratingrelay.engine.normalize import (
    track_key,
    variant_form,
    variant_key,
)
from ratingrelay.engine.types import Candidate, Track

MAX_PROPOSALS = 5
_LEADING_ARTICLE = re.compile(r"^the\s+")


class MatchTier(str, Enum):
    """Which rung of the ladder produced a decision."""

    MBID = "mbid"
    EXACT = "exact"
    VARIANT = "variant"
    CONTAINMENT = "containment"


class MatchStatus(str, Enum):
    """A decision, not an error: ambiguous and unmatched both mean "ask a human"."""

    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    """The result of matching one source track against a target's candidates."""

    status: MatchStatus
    tier: Optional[MatchTier] = None
    matches: tuple[Candidate, ...] = ()
    candidates: tuple[Candidate, ...] = ()

    @property
    def matched(self) -> bool:
        return self.status is MatchStatus.MATCHED


def _contains(left: str, right: str) -> bool:
    """Bidirectional containment on non-empty strings."""
    return bool(left) and bool(right) and (left in right or right in left)


def _artist_form(name: str) -> str:
    """Variant form of an artist name, ignoring a leading article.

    Artist names are compared for equality rather than containment: "Nirvana"
    is a substring of "Nirvana Tribute Band", and a tribute act is exactly the
    kind of candidate that must go to the unmatched queue instead of being
    written to. "The Beatles" and "Beatles" still line up.
    """
    return _LEADING_ARTICLE.sub("", variant_form(name))


def _containment_matches(source: Track, candidates: Sequence[Candidate]) -> list[Candidate]:
    source_title = variant_form(source.title)
    source_artist = _artist_form(source.artist)
    return [
        candidate
        for candidate in candidates
        if _artist_form(candidate.artist) == source_artist
        and _contains(source_title, variant_form(candidate.title))
    ]


def _proposals(source: Track, candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """Plausible neighbours to attach to an unmatched entry.

    Never written to: the unmatched queue shows them so a human can resolve the
    track with one click instead of retyping artist and title.
    """
    source_variant = variant_key(source.artist, source.title)
    source_title = variant_form(source.title)
    proposals = [
        candidate
        for candidate in candidates
        if variant_key(candidate.artist, candidate.title) == source_variant
        or variant_form(candidate.title) == source_title
    ]
    return tuple(proposals[:MAX_PROPOSALS])


def _decide(
    matches: Sequence[Candidate],
    tier: MatchTier,
    *,
    allow_multiple: bool = False,
) -> Optional[MatchOutcome]:
    """Turn a rung's hits into a decision, refusing to choose between loose ones.

    ``allow_multiple`` is set on the rungs where several hits are copies of the
    same recording (MBID, exact). On the loose rungs a second hit means the
    ladder cannot tell them apart, which is a decision for a human.
    """
    if not matches:
        return None
    if len(matches) > 1 and not allow_multiple:
        return MatchOutcome(
            MatchStatus.AMBIGUOUS, tier, candidates=tuple(matches)
        )
    return MatchOutcome(MatchStatus.MATCHED, tier, tuple(matches))


def _mbid_matches(source: Track, candidates: Sequence[Candidate]) -> list[Candidate]:
    if not source.recording_mbid:
        return []
    return [
        candidate
        for candidate in candidates
        if candidate.recording_mbid == source.recording_mbid
    ]


def _exact_matches(source: Track, candidates: Sequence[Candidate]) -> list[Candidate]:
    source_key = track_key(source.artist, source.title)
    return [
        candidate
        for candidate in candidates
        if track_key(candidate.artist, candidate.title) == source_key
    ]


def _variant_matches(source: Track, candidates: Sequence[Candidate]) -> list[Candidate]:
    source_variant = variant_key(source.artist, source.title)
    return [
        candidate
        for candidate in candidates
        if variant_key(candidate.artist, candidate.title) == source_variant
    ]


def match_track(
    source: Track,
    candidates: Sequence[Candidate],
    *,
    allow_fuzzy: bool = True,
) -> MatchOutcome:
    """Resolve *source* against *candidates* using the matching ladder.

    ``allow_fuzzy=False`` stops after the exact rung and reports ``NONE`` with
    proposals, which is what "wait for a human" looks like for an import path
    that should not guess at all.
    """
    if not source.artist.strip() or not source.title.strip():
        return MatchOutcome(status=MatchStatus.NONE)

    candidates = tuple(candidates)

    certain = _decide(
        _mbid_matches(source, candidates), MatchTier.MBID, allow_multiple=True
    ) or _decide(_exact_matches(source, candidates), MatchTier.EXACT, allow_multiple=True)
    if certain is not None:
        return certain

    if not allow_fuzzy:
        return MatchOutcome(MatchStatus.NONE, candidates=_proposals(source, candidates))

    for tier, find in (
        (MatchTier.VARIANT, _variant_matches),
        (MatchTier.CONTAINMENT, _containment_matches),
    ):
        outcome = _decide(find(source, candidates), tier)
        if outcome is not None:
            return outcome

    return MatchOutcome(MatchStatus.NONE, candidates=_proposals(source, candidates))
