"""MusicBrainz: the one place a recording MBID can come from.

Plex knows tracks by a *track* MBID (per file), while ListenBrainz feedback is
keyed by *recording* MBID. This module is the conversion, and it lives in the
engine so that both services can depend on it without depending on each other.
"""

from typing import Optional

import musicbrainzngs as mbz

from ratingrelay.engine.thread import run_sync

mbz.set_useragent("RatingRelay", "2.0", "https://github.com/hnolan/ratingrelay2")


def _lookup(track_mbid: Optional[str], artist: str, title: str) -> Optional[str]:
    """Resolve a recording MBID from a track MBID, falling back to artist + title.

    A miss is ``None``: an unmatched track goes to the queue rather than being
    guessed at, and a network error is the same as a miss because the caller has
    nothing better to do with the distinction.
    """
    try:
        if track_mbid:
            result = mbz.search_recordings(query=f"tid:{track_mbid}", limit=1)
            recordings = result.get("recording-list", [])
            if recordings:
                return recordings[0].get("id")
        result = mbz.search_recordings(recording=title, artist=artist, limit=5)
        recordings = result.get("recording-list", [])
        if not recordings:
            return None
        lowered_title, lowered_artist = title.lower(), artist.lower()
        for recording in recordings:
            artist_credits = recording.get("artist-credit", [])
            if (
                recording.get("title", "").lower() == lowered_title
                and artist_credits
                and artist_credits[0].get("name", "").lower() == lowered_artist
            ):
                return recording.get("id")
        return recordings[0].get("id")
    except Exception:  # noqa: BLE001 - a lookup failure is a miss, not a crash
        return None


async def resolve_recording_mbid(
    track_mbid: Optional[str], artist: str, title: str
) -> Optional[str]:
    """Async wrapper: the MusicBrainz client is synchronous."""
    return await run_sync(lambda: _lookup(track_mbid, artist, title))
