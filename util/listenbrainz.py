import logging
import musicbrainzngs as mbz
import liblistenbrainz as liblbz
from .custom_types import Track

log = logging.getLogger(__name__)


class ListenBrainz:
    """
    Handles all ListenBrainz operations
    """

    def __init__(self, token: str | None, username: str | None):
        self.token = token
        self.username = username
        if not all(
            val is not None and val != "" for val in (self.token, self.username)
        ):
            raise RuntimeError(
                "One or more ListenBrainz variables are missing.\n"
                "If you intended to use ListenBrainz, "
                "make sure all environment variables are set."
            )
        self.client = self._connect()
        self.loves = self.all_loves()
        self.hates = self.all_hates()
        self.new_love_count = 0
        self.new_hate_count = 0

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
        log.info("Checking API token validity.")
        client.is_token_valid(self.token)
        log.info("Token is valid; successfully connected to ListenBrainz.")
        return client

    def _handle_feedback(self, feedback: str, track: Track):
        """
        Handler method for `love()` and `hate()`. Submits track feedback to
        Listenbrainz.

        Args:
            `feedback`: `love` or `hate`
        """
        if feedback == "love":
            existing_track_val = self.loves
            log_str = "Loving"
            feedback_value = 1
            counter = self.new_love_count
        elif feedback == "hate":
            existing_track_val = self.hates
            log_str = "Hating"
            feedback_value = -1
            counter = self.new_hate_count
        else:
            raise ValueError(f"Feedback value must be 'love' or 'hate', got {feedback}")
        if track in existing_track_val:
            log.info(
                "%s by %s has already been %sd.", track.title, track.artist, feedback
            )
            return

        log.info(
            "%s: %s by %s - Checking for track MBID",
            log_str,
            track.title,
            track.artist,
        )
        mbid = self._get_track_mbid(track)
        if mbid:
            existing_mbids = {t.mbid for t in existing_track_val}
            if mbid in existing_mbids:
                log.info("Track already loved.")
            else:
                log.info("MBID found. Submitting %s to ListenBrainz.", mbid)
                self.client.submit_user_feedback(feedback_value, mbid)
                counter += 1
        else:
            log.info("No MBID found. Unable to submit to ListenBrainz.")

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
        # Tracks from Plex don't have the MBID, so create a new set without the MBID
        if rating == "love":
            lbz_tracks = self.loves
        elif rating == "hate":
            lbz_tracks = self.hates
        else:
            raise ValueError(
                f"Invalid rating type '{rating}' - valid types are 'love' and 'hate'"
            )

        old = {(t.title.lower(), t.artist.lower()) for t in lbz_tracks}
        new = [
            track
            for track in track_list
            if (track.title.lower(), track.artist.lower()) not in old
        ]
        return new

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

    def all_hates(self) -> set[Track]:
        """
        Retrieve all tracks the user has already hated
        """
        return self._all(score=-1)

    def all_loves(self) -> set[Track]:
        """
        Retrieve all tracks the user has already loved
        """
        return self._all(score=1)

    def _all(self, score: int):
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
            user_loves = user_loves["feedback"]
            for track in user_loves:
                try:
                    mbid = track["recording_mbid"]
                    title = track["track_metadata"]["track_name"]
                    artist = track["track_metadata"]["artist_name"]
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

    def _get_track_mbid(self, track: Track) -> str | None:
        """
        Queries MusicBrainz and retrieves matching result
        """
        query = " ".join(str(val) for val in [track.title, track.artist])
        mbz.set_useragent(
            "RatingRelay", "v0.1", contact="https://codeberg.org/hnolan/ratingrelay"
        )
        track_search = mbz.search_recordings(
            query=query, artist=track.artist, recording=track.title
        )
        return self._find_mbid_match(track, track_search["recording-list"])

    @staticmethod
    def _find_mbid_match(track: Track, track_search: list[dict]) -> str | None:
        """
        Attempts to find a matching MBID given a track dict
        and MusicBrainz search results
        """
        for result in track_search:
            # find matching title+artist pair
            try:
                track_title = track.title.lower()
                candidate_title = result["title"].lower()

                track_artist = track.artist.lower()
                candidate_artist = result["artist-credit"][0]["name"].lower()
                if track_title == candidate_title and track_artist == candidate_artist:
                    mbid = result["id"]
                    return mbid
            except (IndexError, KeyError):
                return None
        return None
