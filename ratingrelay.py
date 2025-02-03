"""
ratingrelay - a script to sync Plex tracks rated above a certain threshold
to external services like Last.fm and ListenBrainz
"""
import time
import logging
import time
from os import getenv
from typing import Optional
from dotenv import load_dotenv
from util import ListenBrainz, LastFM, Plex


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

def main():
    start = time.time()

    logging.basicConfig(
        level=logging.INFO,
        # filename="log.txt"
        # format="%(asctime)s - %(levelname)s - %(message)s"
    )
    logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
    logging.getLogger("pylast").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

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

    lfm = LastFM(LFM_USERNAME, LFM_PASSWORD, LFM_TOKEN, LFM_SECRET)
    new_loves = lfm.new_loves(track_list=tracks)
    log.info("Found %s track(s) to submit to LastFM.", len(new_loves))
    for track in new_loves:
        log.info("LastFM: Loving %s by %s", track['title'], track['artist'])
        lfm.love(artist=track['artist'], title=track['title'])

    lbz = ListenBrainz(username=LBZ_USERNAME, token=LBZ_TOKEN)
    for track in tracks:
        lbz.love(track)

    exec_time = time.time() - start
    log.info(
        "SUMMARY:\tExecution took %s seconds\n"
        "- Plex tracks meeting rating threshold: %s\n"
        "- Last.fm newly loved tracks: %s\n"
        "- ListenBrainz newly loved tracks: %s",
        exec_time, len(tracks), lfm.new_count, lbz.new_count
    )


if __name__ == "__main__":
    main()
