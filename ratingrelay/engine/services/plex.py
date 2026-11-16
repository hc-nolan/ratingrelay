"""Plex as a library: a source, a target, or both at once.

Plex is the one service where matching means something before the write: it has
a library we can enumerate, so a track either matches a file in it or it does
not. Plex's own MBIDs are *track* MBIDs, not recording MBIDs, so they are not
used for identity here; the matching ladder decides.
"""

import json
from dataclasses import dataclass
from typing import Optional

from plexapi.exceptions import BadRequest, NotFound
from plexapi.server import PlexServer

from ratingrelay.engine.credentials import PlexCredentials
from ratingrelay.engine.matching import MatchStatus, match_track
from ratingrelay.engine.plan import Existing, WritePlan
from ratingrelay.engine.services import MappedTarget
from ratingrelay.engine.store import MappingStore, SyncedTrack
from ratingrelay.engine.thread import run_sync
from ratingrelay.engine.types import (
    DEFAULT_LOVE_RATING,
    Candidate,
    Intent,
    IntentKind,
    OutcomeStatus,
    Reason,
    Track,
)
from ratingrelay.settings import logger

# Plex's own rating comparison operators, as its filter syntax spells them.
_RATING_OPERATORS = {"gte": "userRating>>=", "lte": "userRating<<="}


def _plex_mbid(plex_track) -> Optional[str]:
    for guid in plex_track.guids:
        if guid.id.startswith("mbid://"):
            return guid.id.removeprefix("mbid://")
    return None


def _to_track(plex_track) -> Track:
    return Track(
        artist=plex_track.grandparentTitle or "",
        title=plex_track.title or "",
        track_mbid=_plex_mbid(plex_track),
        rating=plex_track.userRating,
        external_id=str(plex_track.ratingKey),
    )


def _to_candidate(plex_track) -> Candidate:
    # recording_mbid stays None: Plex guids identify the file's track MBID.
    return Candidate(
        artist=plex_track.grandparentTitle or "",
        title=plex_track.title or "",
        rating=plex_track.userRating,
        external_id=str(plex_track.ratingKey),
    )


def _passes_rating(rating: Optional[float], threshold: float, comparison: str) -> bool:
    """Whether a rating meets *threshold* in the *comparison* direction.

    Unrated tracks count as 0 and never pass: "rate nothing above 8" must not
    turn into "rate the whole library".
    """
    value = rating or 0
    if value <= 0:
        return False
    return value >= threshold if comparison == "gte" else value <= threshold


def _rated(rating: Optional[float]) -> bool:
    """Whether a copy carries a rating at all: to us, Plex's 0 is unrated."""
    return rating is not None and rating > 0


class PlexLibrary:
    """An async facade over one Plex library.

    The connection and the library listing are both cached: a run matches every
    source track against the same library, and a reset walks it the same way.
    One instance is built per run (``core/factory.py``), so the cache never
    outlives the run that filled it. Matching a target track against an uncached
    listing would download the whole library once per source track.
    """

    def __init__(self, credentials: PlexCredentials):
        self._credentials = credentials
        self._section = None
        self._listing: Optional[list] = None
        self._tracks: Optional[dict[str, object]] = None
        self._candidates: Optional[tuple[Candidate, ...]] = None

    async def section(self):
        """The Plex library, connected on first use."""
        if self._section is None:
            creds = self._credentials
            server = await run_sync(lambda: PlexServer(creds.server_url, creds.token))
            self._section = await run_sync(lambda: server.library.section(creds.library))
        return self._section

    async def tracks(self) -> list:
        """Every track in the library, fetched once per instance."""
        if self._listing is None:
            section = await self.section()
            self._listing = await run_sync(lambda: list(section.searchTracks()))
        return self._listing

    async def search(self, **filters) -> list:
        """Ask the server for the tracks matching *filters*.

        The filters are Plex's own, so the server does the work: the whole
        library is never fetched just to be filtered here.
        """
        section = await self.section()
        return await run_sync(lambda: list(section.searchTracks(**filters)))

    async def index(self) -> dict[str, object]:
        """``{ratingKey: plexapi track}``, built once per run."""
        if self._tracks is None:
            self._tracks = {str(track.ratingKey): track for track in await self.tracks()}
        return self._tracks

    async def rated(self, threshold: float, comparison: str) -> list:
        """Tracks whose rating passes *threshold* in the *comparison* direction.

        Plex applies the comparison server-side (``userRating>>=``); the local
        check after it is a guard, not the filter, in case a server ignores an
        operator we did not expect it to know.
        """
        operator = _RATING_OPERATORS[comparison]
        try:
            tracks = await self.search(**{operator: threshold})
        except (BadRequest, NotFound) as exc:  # an unknown operator must not fail the run
            logger.warning(
                "Plex rejected the %s filter (%s); falling back to a local filter",
                operator,
                exc,
            )
            tracks = await self.tracks()
        return [t for t in tracks if _passes_rating(t.userRating, threshold, comparison)]

    async def candidates(self) -> tuple[Candidate, ...]:
        """The library as match candidates, built once per instance."""
        if self._candidates is None:
            self._candidates = tuple(_to_candidate(track) for track in await self.tracks())
        return self._candidates

    async def rate(self, external_id: str, rating: Optional[float]) -> None:
        """Set a track's rating by its Plex rating key.

        Clearing the rating of a track that has left the library is a no-op: its
        rating went with it, and failing would keep the record — and every run
        partial — forever. Setting one on a missing track is still an error.
        """
        plex_track = (await self.index()).get(external_id)
        if plex_track is None:
            if rating is None:
                logger.info("Plex track %s is gone; nothing to un-rate", external_id)
                return
            raise KeyError(f"Plex track {external_id} is not in the library")
        await run_sync(lambda: plex_track.rate(rating))

    async def current_rating(self, external_id: str) -> Optional[float]:
        """A track's rating as of this run's listing, or ``None`` if unrated or gone."""
        plex_track = (await self.index()).get(external_id)
        return getattr(plex_track, "userRating", None) if plex_track is not None else None


@dataclass
class PlexSource:
    """Rated tracks from a Plex library.

    ``service`` is the account's ref (``plex``, ``plex:attic``), not just the
    kind: it is half of the key the sync store is written under, so two Plex
    servers never reconcile against each other's writes.
    """

    library: PlexLibrary
    threshold: float = DEFAULT_LOVE_RATING
    comparison: str = "gte"
    service: str = "plex"

    async def fetch(self) -> list[Track]:
        rated = await self.library.rated(self.threshold, self.comparison)
        return [_to_track(track) for track in rated]


def _same_rating(a: Optional[float], b: Optional[float]) -> bool:
    """Equal ratings, with unrated as 0 and room for float noise from the server."""
    return abs((a or 0) - (b or 0)) < 0.01


class PlexTarget(MappedTarget):
    """Writes a rating to every copy of a track that does not already hold it.

    "Holds it" depends on the relay's direction: a love relay wants a copy rated
    *at least* the rating, a hate relay *at most* (and rated at all). A copy
    already on the right side is the user's and is left untouched — a love
    relay writing 10 must not bump a 4-star track the user chose, and one
    writing 8 must not lower a 5-star one.
    """

    # The service this class speaks to, whatever account it is pointed at.
    KIND = "plex"

    def __init__(
        self,
        library: PlexLibrary,
        mappings: MappingStore,
        rating: float = DEFAULT_LOVE_RATING,
        direction: str = "love",
        service: str = KIND,
    ):
        super().__init__(mappings)
        self.service = service
        self._library = library
        self._rating = rating
        self._direction = direction

    def holds(self, current: Optional[float]) -> bool:
        """Whether a copy rated *current* already holds this target's intent."""
        if self._direction == "hate":
            return current is not None and 0 < current <= self._rating
        return current is not None and current >= self._rating

    async def plan(self, track: Track) -> WritePlan:
        artist, title, _ = await self.mapped(track)
        outcome = match_track(
            Track(artist=artist, title=title), await self._library.candidates()
        )

        if outcome.status is MatchStatus.NONE:
            return WritePlan(
                track,
                self.service,
                OutcomeStatus.UNMATCHED,
                Reason.NO_MATCH,
                "No match found in Plex library",
                matches=outcome.candidates,
            )
        if outcome.status is MatchStatus.AMBIGUOUS:
            return WritePlan(
                track,
                self.service,
                OutcomeStatus.UNMATCHED,
                Reason.AMBIGUOUS_MATCH,
                "Ambiguous match in Plex library",
                matches=outcome.candidates,
            )

        rating = self._rating
        external_ids = [match.external_id for match in outcome.matches]
        target_key = json.dumps(external_ids)
        prior = {match.external_id: match.rating for match in outcome.matches}
        to_write = [key for key in external_ids if not self.holds(prior[key])]
        # A copy rated *something* is a copy a person chose for: only a track
        # with no rated copy at all is one whose write was simply removed.
        occupied = any(_rated(prior[key]) for key in external_ids)

        async def commit() -> None:
            for external_id in to_write:
                await self._library.rate(external_id, rating)

        return WritePlan(
            track,
            self.service,
            matches=outcome.matches,
            intent=Intent(IntentKind.RATING, rating),
            target_key=target_key,
            mapped=await self.has_mapping(track),
            existing=Existing(
                holds=not to_write, prior=json.dumps(prior), occupied=occupied
            ),
            commit=commit,
        )

    @staticmethod
    def _rating_keys(record: SyncedTrack) -> list[str]:
        """The copies we rated, or ``ValueError`` if the record does not say."""
        if not record.target_key:
            raise ValueError("no recorded Plex rating keys")
        return list(json.loads(record.target_key))

    @staticmethod
    def undoable(record: SyncedTrack) -> None:
        """Raise exactly when :meth:`undo` would refuse this record.

        Static so that "could we undo this?" can be answered from the record
        alone, without building a client or touching the network.
        """
        PlexTarget._rating_keys(record)

    async def undo(self, record: SyncedTrack) -> None:
        """Put back what each copy held before we rated it.

        A copy whose rating is no longer the one we wrote was changed by the user
        after us, so it is theirs and is left alone. A record without a prior
        (written before priors were kept) clears, as it always did. A copy that
        has left the library is skipped by :meth:`PlexLibrary.rate`.
        """
        prior = json.loads(record.prior) if record.prior else {}
        for external_id in self._rating_keys(record):
            current = await self._library.current_rating(external_id)
            if record.rating is not None and not _same_rating(current, record.rating):
                if external_id in prior and _same_rating(current, prior[external_id]):
                    continue  # never written (it already held it); nothing to undo
                logger.info(
                    "Plex track %s is rated %s, not the %s we wrote; leaving it",
                    external_id,
                    current,
                    record.rating,
                )
                continue
            await self._library.rate(external_id, prior.get(external_id))
