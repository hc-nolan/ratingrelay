"""MusicBrainz: the one place a recording MBID can come from.

Plex knows tracks by a *track* MBID (per file), while ListenBrainz feedback is
keyed by *recording* MBID. This module is the conversion, and it lives in the
engine so that both services can depend on it without depending on each other.
"""

from typing import Optional

import musicbrainzngs as mbz

from ratingrelay.engine.matching import match_track
from ratingrelay.engine.thread import run_sync
from ratingrelay.engine.types import Candidate, Track

mbz.set_useragent("RatingRelay", "2.0", "https://github.com/hnolan/ratingrelay2")

SEARCH_LIMIT = 10


def _candidate(recording: dict) -> Optional[Candidate]:
    """One search result as a candidate, or ``None`` when it cannot be compared."""
    credits = recording.get("artist-credit") or []
    try:
        artist = credits[0]["name"]
    except (IndexError, KeyError, TypeError):
        return None
    if not artist or not recording.get("title"):
        return None
    return Candidate(artist=artist, title=recording["title"], recording_mbid=recording.get("id"))


def _lookup(track_mbid: Optional[str], artist: str, title: str) -> Optional[str]:
    """Resolve a recording MBID from a track MBID, falling back to artist + title.

    A miss is ``None``: an unmatched track goes to the queue rather than being
    guessed at, and a network error is the same as a miss because the caller has
    nothing better to do with the distinction.

    The artist/title search only accepts the *exact* rung of the matching ladder.
    MusicBrainz already ranks its results fuzzily, so taking the first one would
    turn its guess into a love on the wrong recording; the value of this lookup is
    that it is either right or empty.
    """
    try:
        if track_mbid:
            result = mbz.search_recordings(query=f"tid:{track_mbid}", limit=1)
            recordings = result.get("recording-list", [])
            if recordings:
                return recordings[0].get("id")

        result = mbz.search_recordings(
            recording=title, artist=artist, limit=SEARCH_LIMIT
        )
        candidates = [
            candidate
            for candidate in (_candidate(r) for r in result.get("recording-list", []))
            if candidate is not None and candidate.recording_mbid
        ]
        if not candidates:
            return None
        outcome = match_track(
            Track(artist=artist, title=title), candidates, allow_fuzzy=False
        )
        if not outcome.matched:
            return None
        return outcome.matches[0].recording_mbid
    except Exception:  # noqa: BLE001 - a lookup failure is a miss, not a crash
        return None


async def resolve_recording_mbid(
    track_mbid: Optional[str], artist: str, title: str
) -> Optional[str]:
    """Async wrapper: the MusicBrainz client is synchronous."""
    return await run_sync(lambda: _lookup(track_mbid, artist, title))
