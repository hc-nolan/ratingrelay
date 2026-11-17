"""MusicBrainz: the one place a recording MBID can come from.

Plex knows tracks by a *track* MBID (per file), while a rating on ListenBrainz or
hyperfix is keyed by *recording* MBID. This module is the conversion, and it lives
in the engine so that both services can depend on it without depending on each
other. :class:`MbidTarget` is the same conversion as a target needs it: the ladder
that decides which recording a track is, and the record that says what was written.
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import musicbrainzngs as mbz

from ratingrelay.engine import http_timeouts
from ratingrelay.engine.matching import match_track
from ratingrelay.engine.plan import WritePlan
from ratingrelay.engine.services import MappedTarget
from ratingrelay.engine.store import MbidCache, NullMbidCache, SyncedTrack
from ratingrelay.engine.thread import run_sync
from ratingrelay.engine.types import (
    Candidate,
    OutcomeStatus,
    Reason,
    Track,
)

http_timeouts.install()
mbz.set_useragent("RatingRelay", "2.0", "https://github.com/hnolan/ratingrelay2")

SEARCH_LIMIT = 10

Resolver = Callable[[Optional[str], str, str], Awaitable["Resolution"]]


@dataclass(frozen=True, slots=True)
class Resolution:
    """A recording MBID, or the neighbours that were close but not close enough.

    The proposals exist so a failed match is actionable: the ladder already knows
    which recordings nearly lined up, and throwing them away leaves a human with
    nothing to click and everything to retype.
    """

    mbid: Optional[str] = None
    proposals: tuple[Candidate, ...] = ()


def _candidate(recording: dict) -> Optional[Candidate]:
    """One search result as a candidate, or ``None`` when it cannot be compared."""
    artist_credits = recording.get("artist-credit") or []
    try:
        artist = artist_credits[0]["name"]
    except (IndexError, KeyError, TypeError):
        return None
    if not artist or not recording.get("title"):
        return None
    return Candidate(artist=artist, title=recording["title"], recording_mbid=recording.get("id"))


def _lookup(track_mbid: Optional[str], artist: str, title: str) -> Resolution:
    """Resolve a recording MBID from a track MBID, falling back to artist + title.

    A miss is an empty :class:`Resolution`: an unmatched track goes to the queue
    rather than being guessed at. A network failure is *not* a miss: it is raised
    as ``ConnectionError`` so the engine retries it and, failing that, fails the
    track — an outage must not fill the queue with tracks that have an MBID.

    The artist/title search only accepts the *exact* rung of the matching ladder.
    MusicBrainz already ranks its results fuzzily, so taking the first one would
    turn its guess into a love on the wrong recording; the value of this lookup is
    that it is either right or empty. What the looser rungs would have said is
    returned as proposals, which the queue offers to a human instead.
    """
    try:
        if track_mbid:
            result = mbz.search_recordings(query=f"tid:{track_mbid}", limit=1)
            recordings = result.get("recording-list", [])
            if recordings:
                return Resolution(recordings[0].get("id"))

        result = mbz.search_recordings(
            recording=title, artist=artist, limit=SEARCH_LIMIT
        )
        candidates = [
            candidate
            for candidate in (_candidate(r) for r in result.get("recording-list", []))
            if candidate is not None and candidate.recording_mbid
        ]
        if not candidates:
            return Resolution()
        outcome = match_track(
            Track(artist=artist, title=title), candidates, allow_fuzzy=False
        )
        if not outcome.matched:
            return Resolution(proposals=outcome.candidates)
        return Resolution(outcome.matches[0].recording_mbid)
    except mbz.NetworkError as exc:
        # musicbrainzngs has already retried its own transient errors by now.
        raise ConnectionError(f"MusicBrainz: {exc}") from exc
    except mbz.MusicBrainzError:
        # A refused query (400/404) or an unreadable answer: nothing to retry.
        return Resolution()


async def resolve_recording_mbid(
    track_mbid: Optional[str], artist: str, title: str
) -> Resolution:
    """Async wrapper: the MusicBrainz client is synchronous."""
    return await run_sync(lambda: _lookup(track_mbid, artist, title))


class MbidTarget(MappedTarget):
    """A target whose writes are keyed by recording MBID.

    ListenBrainz feedback and hyperfix ratings are both stored against a recording,
    so both have to answer "which recording is this track?" before they write
    anything. That question has one answer in one place (:meth:`resolution`); what
    the answer becomes — a score, a 0-100 rating — is the subclass's business.
    """

    # The service this class speaks to, whatever account it is pointed at.
    KIND = ""

    def __init__(
        self,
        mappings,
        resolve: Resolver,
        cache: Optional[MbidCache] = None,
        *,
        service: str = "",
    ):
        super().__init__(mappings)
        self.service = service or self.KIND
        self._resolve = resolve
        self._cache = cache or NullMbidCache()

    async def resolution(self, track: Track) -> Resolution:
        """The recording this track is, or the ones it was nearly.

        Order is authority: what the source said, then what a human decided,
        then what we resolved before, then the network. What we resolved before
        (the recorded MBID and the cache, both keyed by the *source* track) sits
        below the mapping on purpose — it answered for the unmapped name, so
        where a mapping exists it is not consulted at all; a cache that
        outranked a user's correction would make the correction stop working.
        """
        artist, title, mapped_mbid = await self.mapped(track)
        if track.recording_mbid:
            return Resolution(track.recording_mbid)
        if mapped_mbid:
            return Resolution(mapped_mbid)
        if await self.has_mapping(track):
            return await self._resolve(track.track_mbid, artist, title)
        if track.known_recording_mbid:
            return Resolution(track.known_recording_mbid)
        cached = await self._cache.get(track.key)
        if cached:
            return Resolution(cached)
        return await self._resolve(track.track_mbid, artist, title)

    def unmatched(self, track: Track, resolution: Resolution) -> WritePlan:
        """The plan for a track no recording could be found for.

        One message, in one place: "no match" reads the same whether the target
        stores a score or a rating, and the queue offers the same near misses.
        """
        return WritePlan(
            track,
            self.service,
            OutcomeStatus.UNMATCHED,
            Reason.NO_RECORDING_MBID,
            "No MusicBrainz recording ID found",
            matches=resolution.proposals,
        )

    async def remembered(self, track: Track, mbid: str) -> None:
        """Remember what we wrote, so the next run does not search again."""
        await self._cache.put(track.key, track.artist, track.title, mbid)

    @staticmethod
    def _recorded_mbid(record: SyncedTrack) -> str:
        """The recording we wrote to, or ``ValueError`` if unrecorded.

        Both services key their store by recording MBID, so a row written before
        ``target_key`` existed is still undoable when it recorded the MBID.
        """
        mbid = record.target_key or record.recording_mbid
        if not mbid:
            raise ValueError("no recorded recording MBID")
        return mbid

    @staticmethod
    def undoable(record: SyncedTrack) -> None:
        """Raise exactly when :meth:`undo` would refuse this record.

        Static so that "could we undo this?" can be answered from the record
        alone, without building a client or touching the network.
        """
        MbidTarget._recorded_mbid(record)
