import time
import logging
from urllib3.exceptions import ResponseError
from .services import Services
from .plex import Plex, PlexTrack
from .listenbrainz import ListenBrainz
from .lastfm import LastFM
from .track import Track

log = logging.getLogger("ratingrelay")


def reset_lbz(lbz: ListenBrainz):
    """Reset all loved and hated tracks on ListenBrainz"""
    loves = lbz.all_loves()
    log.info(f"ListenBrainz: {len(loves)} tracks to unlove")

    hates = lbz.all_hates()
    log.info(f"ListenBrainz: {len(hates)} tracks to unhate")

    tracks_to_reset = loves + hates
    length = len(tracks_to_reset)
    i = 0
    for track in tracks_to_reset:
        i += 1
        reset_track_log(index=i, length=length, track=track)
        try:
            lbz.client.submit_user_feedback(0, track.mbid)
        except ResponseError as e:
            if "429" in str(e):
                log.warning("Rate limited, waiting 60s")
                time.sleep(60)
                lbz.client.submit_user_feedback(0, track.mbid)
            else:
                raise


def reset_lfm(lfm: LastFM):
    """Reset all loved tracks on LastFM"""
    loves = lfm.all_loves()
    log.info(f"Last.FM: {len(loves)} tracks to unlove")
    length = len(loves)
    i = 0
    for track in loves:
        i += 1
        reset_track_log(index=i, length=length, track=track)
        lfm.reset(track)


def reset_plex(plex: Plex):
    """Reset all loved tracks on Plex"""
    loves = plex.get_loved_tracks()
    log.info(f"Plex: {len(loves)} tracks to unlove")

    if plex.hate_threshold is not None:
        hates = plex.get_hated_tracks()
        log.info(f"Plex: {len(loves)} tracks to unhate")
    else:
        hates = []

    tracks_to_reset = loves + hates
    length = len(tracks_to_reset)
    i = 0
    for track in tracks_to_reset:
        i += 1
        reset_track_log(index=i, length=length, track=track)
        plex.submit_rating(track=track, rating=None)


def reset_track_log(index: int, length: int, track: any):
    match track:
        case PlexTrack():
            title = track.title
            artist = track.artist().title
        case Track():
            title = track.title
            artist = track.artist
    log.info(f"({index} / {length}): Resetting {title} by {artist}")


def reset(services: Services):
    """
    Reset all ratings submitted to ListenBrainz or Last.fm
    """
    reset_plex(services.plex)
    if services.lbz:
        reset_lbz(services.lbz)
    if services.lfm:
        reset_lfm(services.lfm)
