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
PLEX_LOVE_THRESHOLD = env.get_required_int("LOVE_THRESHOLD")
PLEX_HATE_THRESHOLD = env.get("HATE_THRESHOLD")

BIDIRECTIONAL = env.get_required_bool("BIDIRECTIONAL")


def to_Track_list(t: list[PlexTrack]) -> list[Track]:
    """
    Converts a list of tracks from Plex into a list of `Track` types

    Args:
        `t`: List containing one or more `plexapi.audio.Track` aka `PlexTrack`

    Returns:
        List of `Track` items corresponding to the `PlexTrack` items
    """  # noqa
    return [try_to_make_Track(tr) for tr in t]


def try_to_make_Track(plex_track: PlexTrack) -> Track:
    try:
        return Track(title=plex_track.title, artist=plex_track.artist().title)
    except Exception as e:
        pass


class Relay:
    """
    Handles the main application setup & execution
    """

    def __init__(
        self,
        plex: Plex,
        lfm: Optional[LastFM] = None,
        lbz: Optional[ListenBrainz] = None,
        bidirectional: bool = False,
    ):
        self.start_time = time.time()
        self.bidirectional = bidirectional
        self.plex = plex
        if lfm is None and lbz is None:
            raise RuntimeError(
                "At least one external service required to sync "
                "to/from in order to run the application."
            )
        self.services = {"lfm": lfm, "lbz": lbz}

    def _print_summary(self, exec_time: float, stats_from: dict, stats_to: dict):
        """
        Prints execution summary.

        Args:
            exec_time: Executiom time
            stats_from: Stats dictionary returned by Relay.sync_from_plex()
            stats_to: Stats dictionary returned by Relay.sync_to_plex()
        """
        log.info("EXECUTION SUMMARY:\t%s seconds", exec_time)
        log.info(
            "Plex returned:\t%s loved tracks\t%s hated tracks",
            stats_from["love"].get("plex_tracks"),
            stats_from["hate"].get("plex_tracks"),
        )
        log.info(
            "ListenBrainz:\t%s new loves\t%s new hates",
            stats_from["love"].get("ListenBrainz_new"),
            stats_from["hate"].get("ListenBrainz_new"),
        )
        log.info("LastFM:\t%s new loves", stats_from["love"].get("LastFM_new"))
        log.info(
            "Plex:\t%s new loves\t%s new hates",
            stats_to.get("plex_new_loves"),
            stats_to.get("plex_new_hates"),
        )

    def sync(self):
        """
        Performs synchronization between Plex and external services
        """
        try:
            stats_from = self.sync_from_plex()
        except Exception as e:
            log.warning("Exception raised while trying to sync from Plex: %s", e)
            stats_from = {"love": None, "hate": None}

        stats_to = {}
        if self.bidirectional:
            try:
                stats_to = self.sync_to_plex()
            except Exception as e:
                log.warning("Exception raised while trying to sync to Plex: %s", e)

        exec_time = time.time() - self.start_time
        self._print_summary(exec_time, stats_from, stats_to)

    def sync_from_plex(self) -> dict:
        """
        Sync Plex track ratings to external services

        Returns:
            dict: Dictionary of the following form:
                {
                    "love": {
                        "plex_tracks": int,
                        "LastFM_new": int,
                        "ListenBrainz_new": int
                    },
                    "hate": {
                        "plex_tracks": int,
                        "ListenBrainz_new": int
                    }
                }
        """
        log.info("Starting sync from Plex to external services.")
        try:
            love_info = self._loves_from_plex()
        except Exception as e:
            love_info = {}
            log.warning("Exception raised while trying to sync loves from Plex: %s", e)
        try:
            hate_info = self._hates_from_plex()
        except Exception as e:
            hate_info = {}
            log.warning("Exception raised while trying to sync hates from Plex: %s", e)
        return {"love": love_info, "hate": hate_info}

    def _loves_from_plex(self) -> dict:
        """
        Sync loved tracks from Plex to external services

        Returns:
            dict: Dictionary of the following form:
                {
                    'plex_tracks': int,
                    'LastFM_new': int,
                    'ListenBrainz_new': int
                }
        """  # noqa
        log.info("Querying Plex for tracks meeting the love threshold.")
        tracks = to_Track_list(self.plex.get_loved_tracks())
        log.info("Found %s tracks meeting love threshold.", len(tracks))

        info = {"plex_tracks": len(tracks)}
        for service in self.services.values():
            new_loves = service.new_loves(track_list=tracks)
            log.info("Found %s track(s) to submit to %s", len(new_loves), str(service))
            for track in new_loves:
                log.info("%s: Loving %s by %s", str(service), track.title, track.artist)
                service.love(track)
            info[str(service) + "_new"] = service.new_love_count
        return info

    def _hates_from_plex(self) -> dict:
        """
        Sync hated tracks from Plex to external services (Last.fm not supported)

        Returns:
            dict: Dictionary of the following form:
                {
                    "plex_tracks": int,
                    "ListenBrainz_new": int
                }
        """
        lbz = self.services.get("lbz")
        if lbz is None:
            log.info(
                "ListenBrainz service not configured. Skipping hated tracks (not supported by other services)."
            )
            return {"plex_tracks": 0, "ListenBrainz_new": None}
        log.info("Querying Plex for tracks meeting the hate threshold.")
        tracks = to_Track_list(self.plex.get_hated_tracks())
        log.info("Found %s tracks metting love threshold.", len(tracks))

        new_hates = lbz.new_hates(track_list=tracks)
        log.info("Found %s track(s) to submit to ListenBrainz.", len(new_hates))
        for track in new_hates:
            log.info("ListenBrainz: Hating %s by %s", track.title, track.artist)
            lbz.hate(track)
        return {"plex_tracks": len(tracks), "ListenBrainz_new": lbz.new_hate_count}

    def sync_to_plex(self) -> dict:
        """
        Sync from external services to Plex track ratings
        """
        log.info("Starting sync from external services to Plex.")
        new_loves = self._loves_to_plex()
        new_hates = self._hates_to_plex()
        return {"plex_new_loves": new_loves, "plex_new_hates": new_hates}

    def _loves_to_plex(self) -> int:
        """
        Sync external service loved tracks to Plex track ratings

        Returns:
            - `int` representing Plex's newly loved tracks
        """
        # query external services for loved tracks
        loved_tracks = set()  # should hold Track objects
        plex_new_loves = 0  # counter to track how many were missing from Plex

        for service in self.services.values():
            service_loves = service.all_loves()
            for track in service_loves:
                loved_tracks.add(track)

        # TODO: logging

        for track in loved_tracks:
            matches = self.plex.get_track(track)
            # plex might match multiple tracks for the search
            # so, iterate through the response
            for match in matches:
                if match.userRating is None:
                    self.plex.submit_rating(match, self.plex.love_threshold)
                    plex_new_loves += 1

        return plex_new_loves

    def _hates_to_plex(self) -> int:
        """
        Sync external service hated tracks (Last.fm not supported) to Plex track ratings

        Returns:
            - `int` representing Plex's newly hated tracks
        """
        lbz = self.services.get("lbz")
        if lbz is None:
            log.info(
                "ListenBrainz service not configured. Skipping hated tracks (not supported by other services)."
            )
            return {"plex_new_hates": 0}

        plex_new_hates = 0

        lbz_hates = lbz.all_hates()
        # TODO: logging
        for track in lbz_hates:
            matches = self.plex.get_track(track)
            for match in matches:
                if match.userRating is None:
                    self.plex.submit_rating(match, self.plex.hate_threshold)
                    plex_new_hates += 1

        return plex_new_hates


def setup_services() -> tuple[Plex, Optional[LastFM], Optional[ListenBrainz]]:
    """ """
    plex = Plex(
        music_library=PLEX_LIBRARY,
        love_threshold=PLEX_LOVE_THRESHOLD,
        hate_threshold=PLEX_HATE_THRESHOLD,
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
