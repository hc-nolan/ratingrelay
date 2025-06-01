import logging
import pylast
from .custom_types import Track

log = logging.getLogger(__name__)


class LastFM:
    """
    Class for all LastFM-related operations
    """

    def __init__(
        self,
        username: str | None,
        password: str | None,
        token: str | None,
        secret: str | None,
    ):
        self.username = username
        self.password = password
        self.token = token
        self.secret = secret
        self.client = self._connect()
        self.new_love_count = 0

    def __str__(self):
        return "LastFM"

    def _connect(self) -> pylast.LastFMNetwork:
        """
        Creates a connection to Last.fm using pylast
        """
        if not all(
            val is not None or val != ""
            for val in (self.token, self.secret, self.username, self.password)
        ):
            raise RuntimeError(
                "One or more Last.fm environment variables are missing.\n"
                "If you intended to use Last.fm, make sure all environment variables are set."
            )
        return pylast.LastFMNetwork(
            api_key=self.token,
            api_secret=self.secret,
            username=self.username,
            password_hash=pylast.md5(self.password),
        )

    def love(self, track: Track):
        """
        Loves a single track
        """
        log.info("Loving: %s by %s", track.title, track.artist)
        lastfm_track = self.client.get_track(track.artist, track.title)
        lastfm_track.love()
        self.new_love_count += 1

    def new_loves(self, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex above the love threshold to
        the user's already loved Last.fm tracks

        Args:
            track_list: List of Tracks from Plex to compare against

        Returns:
            list[Track]: List of Tracks that have not been loved yet
        """
        track_list.sort(key=lambda track: track.title)
        # grab tracks user has already loved
        log.info("Grabbing all currently loved tracks from Last.fm.")
        old_loves = self.all_loves()
        # parse into more usable list to match track_list
        old_loves = {(t.title.lower(), t.artist.lower()) for t in old_loves}
        new = [
            track
            for track in track_list
            if (track.title.lower(), track.artist.lower()) not in old_loves
        ]
        log.info("Found %s new tracks to submit to Last.fm.", len(new))
        return new

    def all_loves(self) -> list[Track]:
        """
        Return all currently loved tracks
        """
        track_generator = self.client.get_user(self.username).get_loved_tracks(
            limit=None
        )
        loves = [
            Track(title=t.track.title, artist=t.track.artist.name)
            for t in track_generator
        ]
        return loves
