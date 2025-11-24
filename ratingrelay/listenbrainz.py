from typing import Optional
import logging
import time
import liblistenbrainz as liblbz
import musicbrainzngs as mbz
from .exceptions import ConfigError
from .track import Track
from .config import Settings


log = logging.getLogger("ratingrelay")


class ListenBrainz:
    """
    Handles all ListenBrainz operations
    """

    def __init__(self, settings: Settings):
        self.token = settings.listenbrainz_token
        self.username = settings.listenbrainz_username

        self._check_missing()

        self.client = self._connect()
        self.min_delay: Optional[float] = 0.5
        self.last_req_time: Optional[float] = None

    def _check_missing(self):
        missing = []
        if not self.token:
            missing.append("token")
        if not self.username:
            missing.append("username")
        if missing:
            raise ConfigError(
                f"One or more ListenBrainz variables are missing: {missing}"
            )

    def __str__(self):
        return "ListenBrainz"

    def _connect(self) -> liblbz.ListenBrainz:
        """
        Creates a connection to ListenBrainz
        :return None if any environment variables are missing,
        the ListenBrainz client otherwise
        """
        client = liblbz.ListenBrainz()
        client.set_auth_token(self.token)
        log.info("Checking ListenBrainz API token validity.")
        client.is_token_valid(self.token)
        log.info("Successfully connected to ListenBrainz.")
        return client

    def _handle_feedback(self, feedback: str, track: Track):
        """
        Handler method for `love()` and `hate()`. Submits track feedback to
        Listenbrainz.

        Args:
            `feedback`: One of the following strings: `love`, `hate`
        """
        if feedback == "love":
            log_str = "Loving"
            feedback_value = 1
        elif feedback == "hate":
            log_str = "Hating"
            feedback_value = -1
        else:
            raise ValueError(
                f"Feedback value must be 'love' or 'hate' - got {feedback}"
            )

        if track.mbid is None:
            log.info(
                "%s: %s by %s - Checking for track MBID",
                log_str,
                track.title,
                track.artist,
            )
            mbid = self._get_track_mbid(track)
        else:
            mbid = track.mbid

        if mbid:
            log.info("MBID found. Submitting %s to ListenBrainz.", mbid)
            self._wait_if_needed()
            self.client.submit_user_feedback(feedback_value, mbid)
            self.last_req_time = time.time()
        else:
            log.warning("No MBID found. Unable to submit to ListenBrainz: %s", track)

    def _wait_if_needed(self):
        """
        Used to enforce rate limiting; ensures minimum delay between subsequent
        requests.
        """
        if self.last_req_time is not None:
            last = time.time() - self.last_req_time
            if last < self.min_delay:
                log.info("Waiting...")
                time.sleep(self.min_delay - last)

    def reset(self, track: Track):
        """
        Reset a track's ListenBrainz rating to 0.
        """
        log.info("ListenBrainz - resetting track: %s", track)
        self.client.submit_user_feedback(0, track.get("track_mbid"))

    def love(self, track: Track):
        """
        Love a track on ListenBrainz.
        """
        self._handle_feedback(feedback="love", track=track)

    def hate(self, track: Track):
        """
        Hate a track on ListenBrainz.
        """
        self._handle_feedback(feedback="hate", track=track)

    def _new(self, rating: str, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already loved/hated
        ListenBrainz tracks; returns the tracks that have not yet been loved/hated

        Args:
            `rating`: "love" or "hate"
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `track_list` containing items not in `self.loves`/`self.hates`
        """
        if rating == "love":
            lbz_tracks = self.loves
        elif rating == "hate":
            lbz_tracks = self.hates
        else:
            raise ValueError(
                f"Invalid rating type '{rating}' - valid types are 'love' and 'hate'"
            )

        old = {t.mbid for t in lbz_tracks}
        new = [track for track in track_list if track.mbid not in old]

        return new

    def _old(self, rating: str, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already loved/hated
        ListenBrainz tracks; returns the tracks that have been loved/hated
        on ListenBrainz but no longer meet the rating threshold

        Args:
            `rating`: "love" or "hate"
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `track_list` containing items in `self.loves`/`self.hates` but not in `track_list`
        """  # noqa
        if rating == "love":
            lbz_tracks = self.loves
        elif rating == "hate":
            lbz_tracks = self.hates
        else:
            raise ValueError(
                f"Invalid rating type '{rating}' - valid types are 'love' and 'hate'"
            )

        plex_current = {t.mbid for t in track_list}
        lbz_outdated = [track for track in lbz_tracks if track.mbid not in plex_current]

        return lbz_outdated

    def new_loves(self, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already loved ListenBrainz
        tracks; returns the tracks that have not yet been loved

        Args:
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `track_list` containing items not in `self.loves`
        """
        return self._new(rating="love", track_list=track_list)

    def new_hates(self, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already hated ListenBrainz
        tracks; returns the tracks that have not yet been hated

        Args:
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `track_list` containing items not in `self.hates`
        """
        return self._new(rating="hate", track_list=track_list)

    def old_loves(self, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already loved ListenBrainz
        tracks; returns the tracks that are loved on ListenBrainz but no longer
        meet the rating threshold on Plex.

        Args:
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `self.loves` containing items not in `track_list`
        """
        return self._old(rating="love", track_list=track_list)

    def old_hates(self, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex to already hated ListenBrainz
        tracks; returns the tracks that are hated on ListenBrainz but no longer
        meet the rating threshold on Plex.

        Args:
            `track_list`: List of tracks found from Plex

        Returns:
            Subset of `self.hates` containing items not in `track_list`
        """
        return self._old(rating="hate", track_list=track_list)

    def all_hates(self) -> set[Track]:
        """
        Retrieve all tracks the user has already hated
        """
        self.hates = self._get_all_feedback(score=-1)
        return self.hates

    def all_loves(self) -> set[Track]:
        """
        Retrieve all tracks the user has already loved
        """
        self.loves = self._get_all_feedback(score=1)
        return self.loves

    def _get_all_feedback(self, score: int):
        """
        Retrieve all tracks the user has submitted feedback for

        Args:
            `score`: Integer representing the user feedback; `1` for love, `-1` for hate.

        Returns:
            `set` of all tracks found on ListenBrainz account
        """
        all_loves = set()
        offset = 0
        count = 100

        while True:
            user_loves = self.client.get_user_feedback(
                username=self.username,
                score=score,
                count=count,
                offset=offset,
                metadata=True,
            )
            user_loves = user_loves.get("feedback")
            for track in user_loves:
                try:
                    mbid = track.get("recording_mbid")
                    metadata = track.get("track_metadata")
                    if not metadata:
                        log.warning(f"Found no metadata for recording {mbid}")
                        continue
                    title = metadata.get("track_name")
                    artist = metadata.get("artist_name")
                    track_tuple = Track(title=title, artist=artist, mbid=mbid)
                    all_loves.add(track_tuple)
                except TypeError:
                    log.warning(
                        "Malformed data in response from MusicBrainz; "
                        "track title and/or artist unavailable for %s",
                        track["recording_mbid"],
                    )

            if len(user_loves) < count:
                break  # No more feedback to fetch
            offset += count
        return all_loves

    def _get_track_mbid(self, track: Track) -> Optional[str]:
        """
        Queries MusicBrainz and retrieves matching result
        """
        query = " ".join(val for val in [track.title, track.artist])
        track_search = mbz.search_recordings(
            query=query, artist=track.artist, recording=track.title
        )
        return self._find_mbid_match(track, track_search["recording-list"])

    @staticmethod
    def _find_mbid_match(track: Track, track_search: list[dict]) -> Optional[str]:
        """
        Attempts to find a matching MBID given a track dict
        and MusicBrainz search results
        """
        track_artist = track.artist.lower()
        track_title = track.title.lower()
        for result in track_search:
            # find matching title+artist pair
            try:
                candidate_title = result.get("title").lower()

                candidate_artist = result["artist-credit"][0].get("name").lower()
                if track_title == candidate_title and track_artist == candidate_artist:
                    mbid = result["id"]
                    return mbid
            except (IndexError, KeyError, TypeError):
                # These exceptions mean the MBID is missing
                continue
        return None
