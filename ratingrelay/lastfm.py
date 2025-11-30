import logging

import pylast

from .exceptions import ConfigError
from .track import Track
from .config import Settings


log = logging.getLogger("ratingrelay")


class LastFM:
    """
    Class for all LastFM-related operations
    """

    def __init__(self, settings: Settings):
        self.username = settings.lastfm_username
        self.password = settings.lastfm_password
        self.token = settings.lastfm_token
        self.secret = settings.lastfm_secret

        self._check_missing()

        self.client = self._connect()
        self.new_love_count = 0

    def _check_missing(self):
        """
        Check for missing configuration variables
        """
        missing = []
        if not self.token:
            missing.append("token")
        if not self.secret:
            missing.append("secret")
        if not self.username:
            missing.append("username")
        if not self.password:
            missing.append("password")
        if missing:
            raise ConfigError(f"One or more LastFM variables are missing: {missing}")

    def __str__(self):
        return "LastFM"

    def _connect(self) -> pylast.LastFMNetwork:
        """
        Creates a connection to Last.fm using pylast
        """

        lfm = pylast.LastFMNetwork(
            api_key=self.token,
            api_secret=self.secret,
            username=self.username,
            password_hash=pylast.md5(self.password),
        )
        log.info("Successfully authenticated with LastFM.")
        return lfm

    def love(self, track: Track):
        """
        Loves a single track
        """
        log.info(f"Loving: {track.title} by {track.artist}")
        lastfm_track = self.client.get_track(track.artist, track.title)
        lastfm_track.love()
        self.new_love_count += 1

    def reset(self, track: Track):
        """
        Un-loves a single track
        """
        log.info(f"Last.FM - resetting track: {track}")
        lastfm_track = self.client.get_track(track.artist, track.title)
        lastfm_track.unlove()

    def new_loves(self, track_list: list[Track]) -> list[Track]:
        """
        Compares the list of tracks from Plex above the love threshold to
        the user's already loved Last.fm tracks
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
        log.info(f"Found {len(new)} new tracks to submit to Last.fm.")
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
