import pylast

class LastFM:
    """
    Class for all LastFM-related operations
    """
    def __init__(
        self, 
        username: str,
        password: str,
        key: str,
        secret: str
    ):
        self.username = username
        self.password = password
        self.key = key
        self.secret = secret
        self.client = self._connect()
        self.new_count = 0

    def _connect(self) -> pylast.LastFMNetwork:
        """
        Creates a connection to Last.fm using pylast
        """
        if not all(
            val is not None or 
            val != ""
            for val in (
                self.key, self.secret, 
                self.username, self.password
            )
        ):
            raise RuntimeError(
                "One or more Last.fm environment variables are missing.\n"
                "If you intended to use Last.fm, make sure all environment variables are set."
            )
        return pylast.LastFMNetwork(
            api_key=self.key,
            api_secret=self.secret,
            username=self.username,
            password_hash=pylast.md5(self.password)
        )

    def love(self, artist: str, title: str):
        """
        Loves a single track
        """
        lastfm_track = self.client.get_track(artist, title)
        lastfm_track.love()
        self.new_count += 1

    def new_loves(self, track_list: list[dict]) -> list[dict]:
        """
        Compares the list of tracks from Plex above the rating threshold to
        the user's already loved Last.fm tracks
        Returns the tracks that have not been loved yet
        """
        track_list.sort(key=lambda track: track["title"])
        # grab tracks user has already loved
        user = self.client.get_user(self.username)
        old_loves = user.get_loved_tracks(limit=None)
        # parse into more usable list to match track_list
        old_loves = {
            (
                t.track.title.lower(),
                t.track.artist.name.lower()
            )
            for t in old_loves
        }
        return [
            track for track in track_list
            if (track["title"].lower(), track["artist"].lower())  not in old_loves
        ]

