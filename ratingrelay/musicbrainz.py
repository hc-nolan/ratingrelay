import logging
from typing import Optional
import musicbrainzngs as mbz


log = logging.getLogger("ratingrelay")


def query_recording_mbid(
    track_mbid: Optional[str], title: str, artist: str
) -> Optional[str]:
    """
    Queries MusicBrainz API for a track's recording MBID.
    """
    log.info("Searching MusicBrainz for recording MBID.")
    if track_mbid is not None:
        log.info("Using track MBID: %s", track_mbid)
        search = mbz.search_recordings(query=title, artist=artist)
    else:
        log.info("track_mbid is empty, using title and artist: %s - %s", title, artist)
        search = mbz.search_recordings(query=f"tid:{track_mbid}")
    recording = search.get("recording-list")

    if recording == []:
        log.warning("No recordings found on MusicBrainz.")
        rec_mbid = None
    else:
        log.info("Recording MBID found from MusicBrainz search.")
        rec_mbid = recording[0].get("id")

    return rec_mbid
