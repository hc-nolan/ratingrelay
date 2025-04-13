"""
ratingrelay - a script to sync Plex tracks rated above a certain threshold
to external services like Last.fm and ListenBrainz
"""

import time
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from typing import Optional
from plexapi.audio import Track as PlexTrack
from util import ListenBrainz, LastFM, Plex, Track
import util.env as env


log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s:%(module)s:%(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        TimedRotatingFileHandler(
            "ratingrelay.log", when="midnight", interval=30, backupCount=6
        ),
    ],
)
logging.getLogger("musicbrainzngs").setLevel(logging.WARNING)
logging.getLogger("pylast").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

LFM_USERNAME = env.get("LASTFM_USERNAME")
LFM_PASSWORD = env.get("LASTFM_PASSWORD")
LFM_TOKEN = env.get("LASTFM_API_KEY")
LFM_SECRET = env.get("LASTFM_SECRET")


LBZ_USERNAME = env.get("LISTENBRAINZ_USERNAME")
LBZ_TOKEN = env.get("LISTENBRAINZ_TOKEN")

PLEX_URL = env.get_required("SERVER_URL")
PLEX_LIBRARY = env.get_required("MUSIC_LIBRARY")
PLEX_THRESHOLD = env.get_required_int("RATING_THRESHOLD")

BIDIRECTIONAL = env.get_required_bool("BIDIRECTIONAL")


def to_Track_list(t: list[PlexTrack]) -> list[Track]:
    """
    Converts a list of tracks from Plex into a list of `Track` types
    :param `t`: List containing one or more `plexapi.audio.Track` aka `PlexTrack`
    :returns List of `Track` items corresponding to the `PlexTrack` items
    """  # noqa
    return [Track(title=tr.title, artist=tr.artist().title) for tr in t]


class Relay:
    """
    Handles the main application setup & execution
    """

    def __init__(
        self,
        plex: Plex,
        lfm: Optional[LastFM],
        lbz: Optional[ListenBrainz],
        bidirectional: bool = False,
    ):
        self.start_time = time.time()
        self.bidirectional = bidirectional
        self.plex = plex
        self.services = [lfm, lbz]
        if self.services == []:
            raise RuntimeError(
                "At least one external service required to sync "
                "to/from in order to run the application."
            )

    def sync(self):
        stats_from = self.sync_from_plex()
        stats_to = {}
        if self.bidirectional:
            stats_to = self.sync_to_plex()
        exec_time = time.time() - self.start_time
        log.info(
            "SUMMARY:\tExecution took %s seconds\n"
            "- Plex tracks meeting rating threshold: %s\n"
            "- Last.fm newly loved tracks: %s\n"
            "- ListenBrainz newly loved tracks: %s\n"
            "- Tracks that were Loved on external services but not on Plex: %s",
            exec_time,
            stats_from.get("tracks"),
            stats_from["newly_loved_counts"].get("LastFM"),
            stats_from["newly_loved_counts"].get("ListenBrainz"),
            stats_to["plex_new_loves"],
        )

    def sync_from_plex(self) -> dict:
        """
        Sync Plex track ratings to external services as Loved Tracks
        """
        log.info("Starting sync from Plex to external services.")

        log.info("Querying Plex for tracks meeting the rating threshold.")
        tracks = to_Track_list(self.plex.get_tracks())
        log.info("Found %s tracks meeting rating threshold.", len(tracks))

        service_new_counts = []
        for service in self.services:
            new_loves = service.new_loves(track_list=tracks)
            log.info("Found %s track(s) to submit to %s", len(new_loves), str(service))
            for track in new_loves:
                log.info("%s: Loving %s by %s", str(service), track.title, track.artist)
                service.love(track)
            service_new_counts.append({str(service): service.new_count})

        return {"tracks": len(tracks), "newly_loved_counts": service_new_counts}

    def sync_to_plex(self) -> dict:
        """
        Sync Loved Tracks from external services to Plex track ratings
        """
        log.info("Starting sync from external services to Plex.")
        # query external services for loved tracks
        loved_tracks = set()  # should hold Track objects
        plex_new_loves = 0  # counter to track how many were missing from Plex

        for service in self.services:
            service_loves = service.all_loves()
            for track in service_loves:
                loved_tracks.add(track)

        for track in loved_tracks:
            matches = self.plex.get_track(track)
            # plex might match multiple tracks for the search
            # so, iterate through the response
            for match in matches:
                if match.userRating is None:
                    self.plex.submit_rating(match, self.plex.rating_threshold)
                    plex_new_loves += 1

        return {"plex_new_loves": plex_new_loves}


def setup_services() -> tuple[Plex, Optional[LastFM], Optional[ListenBrainz]]:
    """ """
    plex = Plex(
        music_library=PLEX_LIBRARY,
        rating_threshold=PLEX_THRESHOLD,
        url=PLEX_URL,
    )
    try:
        lfm = LastFM(
            username=LFM_USERNAME,
            password=LFM_PASSWORD,
            token=LFM_TOKEN,
            secret=LFM_SECRET,
        )
    except RuntimeError as e:
        log.error(
            "Got a runtime error when attempting to execute Last.fm - skipping Last.fm"
        )
        log.error("Error details:")
        log.error(e)
        log.error("This can be safely ignored if you do not wish to use Last.fm")
        lfm = None

    try:
        lbz = ListenBrainz(username=LBZ_USERNAME, token=LBZ_TOKEN)
    except RuntimeError as e:
        log.error(
            "Got a runtime error when attempting to execute ListenBrainz - skipping ListenBrainz"
        )
        log.error("Error details:")
        log.error(e)
        log.error("This can be safely ignored if you do not wish to use ListenBrainz")
        lbz = None

    return plex, lfm, lbz


def main():
    log.info("Starting RatingRelay")

    plex, lfm, lbz = setup_services()

    relay = Relay(plex, lfm, lbz, BIDIRECTIONAL)
    relay.sync()


if __name__ == "__main__":
    main()
