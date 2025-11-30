import time
from urllib3.exceptions import ResponseError
import logging
from .services import Services

log = logging.getLogger("ratingrelay")


def reset_lbz(lbz):
    """Reset all loved and hated tracks on ListenBrainz"""
    loves = lbz.all_loves()
    log.info(f"ListenBrainz: {len(loves)} tracks to unlove")
    i = 0
    for track in loves:
        i += 1
        log.info(f"{i}/{len(loves)}")
        try:
            lbz.client.submit_user_feedback(0, track.mbid)
        except ResponseError as e:
            if "429" in str(e):
                log.warning("Rate limited, waiting 60s")
                time.sleep(60)
                lbz.client.submit_user_feedback(0, track.mbid)
            else:
                raise

    hates = lbz.all_hates()
    log.info(f"ListenBrainz: {len(hates)} tracks to unhate")
    i = 0
    for track in hates:
        i += 1
        log.info(f"{i}/{len(hates)}")
        try:
            lbz.client.submit_user_feedback(0, track.mbid)
        except ResponseError as e:
            if "429" in str(e):
                log.warning("Rate limited, waiting 60s")
                time.sleep(60)
                lbz.client.submit_user_feedback(0, track.mbid)
            else:
                raise


def reset_lfm(lfm):
    """Reset all loved tracks on LastFM"""
    loves = lfm.all_loves()
    log.info(f"Last.FM: {len(loves)} tracks to unlove")
    i = 0
    for track in loves:
        i += 1
        log.info(f"{i}/{len(loves)}")
        lfm.reset(track)


def reset(services: Services):
    """
    Reset all ratings submitted to ListenBrainz or Last.fm
    """
    if services.lbz:
        reset_lbz(services.lbz)
    if services.lfm:
        reset_lfm(services.lfm)
