"""
ratingrelay - a script to sync Plex tracks rated above a certain threshold
to external services like Last.fm and ListenBrainz
"""
import time
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
import time
from os import getenv
from typing import Optional
from dotenv import load_dotenv
from util import ListenBrainz, LastFM, Plex
from rich.print import print


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

def sync_from_plex() -> dict:
    plex = Plex(
        library=PLEX_LIBRARY,
        threshold=PLEX_THRESHOLD,
        url=PLEX_URL,
        cid=PLEX_CID,
        token=PLEX_TOKEN
    )
    log.info("Querying Plex for tracks meeting the rating threshold.")
    tracks = plex.get_tracks()
    log.info("Found %s tracks meeting rating threshold.", len(tracks))

    lfm = LastFM(
        username=LFM_USERNAME,
        password=LFM_PASSWORD,
        token=LFM_TOKEN,
        secret=LFM_SECRET
    )
    lfm_new_loves = lfm.new_loves(track_list=tracks)
    log.info("Found %s track(s) to submit to LastFM.", len(lfm_new_loves))
    for track in lfm_new_loves:
        log.info("LastFM: Loving %s by %s", track['title'], track['artist'])
        lfm.love(artist=track['artist'], title=track['title'])

    lbz = ListenBrainz(
        username=LBZ_USERNAME,
        token=LBZ_TOKEN
    )
    lbz_new_loves = lbz.new_loves(track_list=tracks)
    log.info("Found %s track(s) to submit to ListenBrainz.", len(lbz_new_loves))
    for track in lbz_new_loves:
        log.info("ListenBrainz: Loving %s by %s", track['title'], track['artist'])
        lbz.love(track)
    return {
        "tracks": len(tracks),
        "lfm_new_count": lfm.new_count,
        "lbz_new_count": lbz.new_count
    }

def sync_to_plex() -> dict:

    return {}

def main():
    start = time.time()

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

    log.info("Starting RatingRelay")
    from_plex_stats = sync_from_plex()
    to_plex_stats = sync_to_plex()

    exec_time = time.time() - start
    log.info(
        "SUMMARY:\tExecution took %s seconds\n"
        "- Plex tracks meeting rating threshold: %s\n"
        "- Last.fm newly loved tracks: %s\n"
        "- ListenBrainz newly loved tracks: %s",
        exec_time, 
        from_plex_stats["tracks"], 
        from_plex_stats["lfm_new_count"],
        from_plex_stats["lbz_new_count"]
    )


if __name__ == "__main__":
    main()
