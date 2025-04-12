"""
ratingrelay - a script to sync Plex tracks rated above a certain threshold
to external services like Last.fm and ListenBrainz
"""
import time
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from os import getenv
from typing import Optional
from dotenv import load_dotenv
from util import ListenBrainz, LastFM, Plex, TrackTuple


def getenv_required(var_name: str) -> str:
    """
    Wraps os.getenv() - raises an exception if value is not present
    """
    value: Optional[str] = getenv(var_name)
    if not value:
        raise ValueError(f"Environment variable {var_name} is not set. "
                         "Please add it and re-run the script.")
    return value


log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s:%(module)s:%(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        TimedRotatingFileHandler(
            "ratingrelay.log",
            when="midnight",
            interval=30,
            backupCount=6
        )
    ]
)
logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
logging.getLogger("pylast").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

load_dotenv()

PLEX_CID = getenv("PLEX_CID")
PLEX_TOKEN = getenv("PLEX_TOKEN")

LFM_USERNAME = getenv("LASTFM_USERNAME")
LFM_PASSWORD = getenv("LASTFM_PASSWORD")
LFM_TOKEN = getenv("LASTFM_API_KEY")
LFM_SECRET = getenv("LASTFM_SECRET")

LBZ_USERNAME = getenv("LISTENBRAINZ_USERNAME")
LBZ_TOKEN = getenv("LISTENBRAINZ_TOKEN")

PLEX_URL = getenv_required("SERVER_URL")
PLEX_LIBRARY = getenv_required("MUSIC_LIBRARY")
PLEX_THRESHOLD = getenv_required("RATING_THRESHOLD")


def sync_from_plex(
    plex: Plex,
    lfm: Optional[LastFM],
    lbz: Optional[ListenBrainz]
) -> dict:
    """
    Sync Plex track ratings to external services as Loved Tracks
    """
    log.info("Starting sync from Plex to external services.")

    log.info("Querying Plex for tracks meeting the rating threshold.")
    tracks = plex.get_tracks()
    log.info("Found %s tracks meeting rating threshold.", len(tracks))

    lfm_new_count = 0
    if lfm:
        lfm_new_loves = lfm.new_loves(track_list=tracks)
        log.info("Found %s track(s) to submit to LastFM.", len(lfm_new_loves))
        for track in lfm_new_loves:
            log.info("LastFM: Loving %s by %s", track['title'], track['artist'])
            lfm.love(artist=track['artist'], title=track['title'])
        lfm_new_count = lfm.new_count

    lbz_new_count = 0
    if lbz:
        lbz_new_loves = lbz.new_loves(track_list=tracks)
        log.info("Found %s track(s) to submit to ListenBrainz.", len(lbz_new_loves))
        for track in lbz_new_loves:
            log.info("ListenBrainz: Loving %s by %s", track['title'], track['artist'])
            lbz.love(track)
        lbz_new_count = lbz.new_count

    return {
        "tracks": len(tracks),
        "lfm_new_count": lfm_new_count,
        "lbz_new_count": lbz_new_count
    }

def sync_to_plex(
    plex: Plex,
    lfm: Optional[LastFM],
    lbz: Optional[ListenBrainz]
) -> dict:
    """
    Sync Loved Tracks from external services to Plex track ratings
    """
    log.info("Starting sync from external services to Plex.")
    # query external services for loved tracks
    loved_tracks = set()    # should hold TrackTuple objects
    plex_new_loves = 0   # counter to track how many were missing from Plex

    if lfm:
        lfm_loves = lfm.all_loves()
        for track in lfm_loves:
            loved_tracks.add(TrackTuple(
                title=track.track.title,
                artist=track.track.artist.name
            ))

    if lbz:
        lbz_loves = lbz.loves
        for track in lbz_loves:
            loved_tracks.add(TrackTuple(
                title=track[1],
                artist=track[2]
            ))

    for track in loved_tracks:
        plex_track = plex.get_track(track)
        # plex might match multiple tracks for the search
        # so, iterate through the response
        if plex_track:
            for match in plex_track:
                if match.get("@userRating") is None:
                    plex.submit_rating(match["@ratingKey"], PLEX_THRESHOLD)
                    plex_new_loves += 1

    return {
        "plex_new_loves": plex_new_loves
    }


def main():
    start = time.time()
    log.info("Starting RatingRelay")

    plex = Plex(
        library=PLEX_LIBRARY,
        threshold=PLEX_THRESHOLD,
        url=PLEX_URL,
        cid=PLEX_CID,
        token=PLEX_TOKEN
    )
    try:
        lfm = LastFM(
            username=LFM_USERNAME,
            password=LFM_PASSWORD,
            token=LFM_TOKEN,
            secret=LFM_SECRET
        )
    except RuntimeError as e:
        log.error("Got a runtime error when attempting to execute Last.fm - skipping Last.fm")
        log.error("Error details:")
        log.error(e)
        log.error("This can be safely ignored if you do not wish to use Last.fm")
        lfm = None

    try:
        lbz = ListenBrainz(
            username=LBZ_USERNAME,
            token=LBZ_TOKEN
        )
    except RuntimeError as e:
        log.error("Got a runtime error when attempting to execute ListenBrainz - skipping ListenBrainz")
        log.error("Error details:")
        log.error(e)
        log.error("This can be safely ignored if you do not wish to use ListenBrainz")
        lbz = None

    if all(x is None for x in [lfm, lbz]):
        raise RuntimeError(
            "Connections to all external services failed. "
            "At least one external service connection is required to run the program. "
            "Please double check .env values."
        )


    from_plex_stats = sync_from_plex(plex, lfm, lbz)
    to_plex_stats = sync_to_plex(plex, lfm, lbz)

    exec_time = time.time() - start
    log.info(
        "SUMMARY:\tExecution took %s seconds\n"
        "- Plex tracks meeting rating threshold: %s\n"
        "- Last.fm newly loved tracks: %s\n"
        "- ListenBrainz newly loved tracks: %s\n"
        "- Tracks that were Loved on external services but not on Plex: %s",
        exec_time,
        from_plex_stats["tracks"],
        from_plex_stats["lfm_new_count"],
        from_plex_stats["lbz_new_count"],
        to_plex_stats["plex_new_loves"]
    )


if __name__ == "__main__":
    main()
