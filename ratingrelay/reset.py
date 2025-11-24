import logging
from .services import Services

log = logging.getLogger("ratingrelay")


def reset_lbz(lbz):
    """Reset all loved and hated tracks on ListenBrainz"""
    loves = lbz.all_loves()
    log.info("ListenBrainz: %s tracks to unlove", len(loves))
    i = 0
    for track in loves:
        i += 1
        log.info("%s/%s", i, len(loves))
        lbz.client.submit_user_feedback(0, track.mbid)
    hates = lbz.all_hates()
    log.info("ListenBrainz: %s tracks to unhate", len(hates))
    i = 0
    for track in hates:
        i += 1
        log.info("%s/%s", i, len(hates))
        lbz.client.submit_user_feedback(0, track.mbid)


def reset_lfm(lfm):
    """Reset all loved tracks on LastFM"""
    loves = lfm.all_loves()
    log.info("Last.FM: %s tracks to unlove", len(loves))
    i = 0
    for track in loves:
        i += 1
        log.info("%s/%s", i, len(loves))
        lfm.reset(track)


def reset(services: Services):
    """
    Reset all ratings submitted to ListenBrainz or Last.fm
    """
    if services.lbz:
        reset_lbz(services.lbz)
    if services.lfm:
        reset_lfm(services.lfm)
