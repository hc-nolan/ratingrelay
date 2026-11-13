"""Plex as a library: a source, a target, or both at once.

Plex is the one service where matching means something before the write: it has
a library we can enumerate, so a track either matches a file in it or it does
not. Plex's own MBIDs are *track* MBIDs, not recording MBIDs, so they are not
used for identity here; the matching ladder decides.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

from plexapi.server import PlexServer

from ratingrelay.engine.credentials import PlexCredentials
from ratingrelay.engine.matching import MatchStatus, match_track
from ratingrelay.engine.plan import WritePlan
from ratingrelay.engine.services import MappedTarget
from ratingrelay.engine.store import MappingStore, SyncedTrack
from ratingrelay.engine.thread import run_sync
from ratingrelay.engine.types import (
    Candidate,
    Intent,
    IntentKind,
    OutcomeStatus,
    Reason,
    Track,
)


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


class PlexLibrary:
    """An async facade over one Plex library.

    The connection and the library listing are both cached: a run matches every
    source track against the same library, and a reset walks it the same way.
    """

    def __init__(self, credentials: PlexCredentials):
        self._credentials = credentials
        self._section = None
        self._tracks: Optional[dict[str, object]] = None

    async def section(self):
        """The Plex library, connected on first use."""
        if self._section is None:
            creds = self._credentials
            server = await run_sync(lambda: PlexServer(creds.server_url, creds.token))
            self._section = await run_sync(lambda: server.library.section(creds.library))
        return self._section

    async def tracks(self) -> list:
        """Every track in the library."""
        section = await self.section()
        return await run_sync(lambda: list(section.searchTracks()))

    async def index(self) -> dict[str, object]:
        """``{ratingKey: plexapi track}``, built once per run."""
        if self._tracks is None:
            self._tracks = {str(track.ratingKey): track for track in await self.tracks()}
        return self._tracks

    async def rated(self, threshold: float, comparison: str) -> list:
        """Tracks whose rating passes *threshold* in the *comparison* direction.

        The filter runs in Python today; asking Plex for ``userRating>>=`` is the
        next slice, and it changes only this method.
        """
        tracks = await self.tracks()
        if comparison == "gte":
            return [t for t in tracks if (t.userRating or 0) >= threshold]
        return [t for t in tracks if 0 < (t.userRating or 0) <= threshold]

    async def candidates(self) -> tuple[Candidate, ...]:
        return tuple(_to_candidate(track) for track in await self.tracks())

    async def rate(self, external_id: str, rating: Optional[float]) -> None:
        """Set a track's rating by its Plex rating key."""
        plex_track = (await self.index())[external_id]
        await run_sync(lambda: plex_track.rate(rating))


@dataclass
class PlexSource:
    """Rated tracks from a Plex library."""

    library: PlexLibrary
    threshold: float = 8.0
    comparison: str = "gte"
    service: str = field(default="plex", init=False)

    async def fetch(self) -> list[Track]:
        rated = await self.library.rated(self.threshold, self.comparison)
        return [_to_track(track) for track in rated]


class PlexTarget(MappedTarget):
    """Writes a rating to every copy of a track in the library."""

    service = "plex"

    def __init__(self, library: PlexLibrary, mappings: MappingStore, rating: float = 10.0):
        super().__init__(mappings)
        self._library = library
        self._rating = rating

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

        async def commit() -> None:
            for external_id in external_ids:
                await self._library.rate(external_id, rating)

        return WritePlan(
            track,
            self.service,
            matches=outcome.matches,
            intent=Intent(IntentKind.RATING, rating),
            target_key=target_key,
            commit=commit,
        )

    async def undo(self, record: SyncedTrack) -> None:
        """Clear the rating on every copy this tool rated, and only those."""
        if not record.target_key:
            raise ValueError("no recorded Plex rating keys")
        for external_id in json.loads(record.target_key):
            await self._library.rate(external_id, None)
