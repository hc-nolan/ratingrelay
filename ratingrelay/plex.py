import logging
from typing import Optional

from rich.prompt import IntPrompt, Prompt
from rich import print
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer
from plexapi.library import LibrarySection
from plexapi.audio import Track as PlexTrack

from .env import Env
from .config import Settings


log = logging.getLogger("ratingrelay")


class Plex:
    """
    Handles all interaction with Plex server
    """

    _RATING_OFFSET = 0.1

    def __init__(self, settings: Settings):
        self.url = str(settings.plex_server_url)
        self.love_threshold = settings.love_threshold
        self.hate_threshold = settings.hate_threshold
        self.token = settings.plex_token
        self._verify_auth()
        self.music_library = self._get_music_library(settings.plex_music_library)

    def _verify_auth(self):
        """
        Checks if self.token is valid for authenticating to Plex.
        If token is not valid, proceeds with interactive authentication
        to retrieve a new token.
        """
        if not self.token:
            log.info(
                "No saved PLEX_TOKEN found. Proceeding with manual authentication."
            )
            self._manual_auth()
        if self._is_token_valid():
            log.info("Successfully authenticated with Plex.")
        else:
            log.info("Saved PLEX_TOKEN is no longer valid. Please re-authenticate.")
            self._manual_auth()

    def _manual_auth(self):
        """
        Handles the manual authentication process.
        Retrieves the valid token after authentication for future use.
        """
        print(
            "Please enter your Plex authentication details. "
            "This should only be required the first time the program is run."
        )

        plex_server = Prompt.ask("Plex server name")
        plex_username = Prompt.ask("Plex username")
        plex_password = Prompt.ask("Plex password (input hidden)", password=True)
        plex_code = IntPrompt.ask(
            "Plex MFA code (leave blank if not using MFA)", default=0
        )

        account = MyPlexAccount(
            username=plex_username, password=plex_password, code=str(plex_code)
        )
        plex = account.resource(plex_server).connect()
        self.server = plex

        self.token = plex._token
        Env.write_var("PLEX_TOKEN", self.token)

    def _is_token_valid(self) -> bool:
        """
        Checks if self.token is valid for authenticating to the Plex server
        """
        log.info("Checking if PLEX_TOKEN is still valid for authentication.")
        try:
            self.server = PlexServer(self.url, self.token)
            return True
        except Exception as e:
            log.error(e)
            return False

    def _get_music_library(self, library_name: str) -> LibrarySection:
        """
        Returns the LibrarySection matching the given library_name.
        Raises plexapi.exceptions.NotFound if no matching library exists.
        """
        return self.server.library.section(library_name)

    def get_loved_tracks(self) -> list[PlexTrack]:
        """
        Queries a given library for all tracks meeting settings.love_threshold
        """

        # The Plex >>= filter is "greater than", so we subtract from the defined
        # threshold value to effectively make it "greater than or equal to"
        thresh = float(self.love_threshold) - self._RATING_OFFSET
        return self.music_library.search(
            libtype="track", filters={"userRating>>=": thresh}
        )

    def get_hated_tracks(self) -> list[PlexTrack]:
        """
        Queries a given library for all tracks meeting settings.hate_threshold
        """

        # The Plex <<= filter is "less than", so we add to the defined
        # threshold value to effectively make it "less than or equal to"
        thresh = float(self.hate_threshold) + self._RATING_OFFSET
        return self.music_library.search(
            libtype="track", filters={"userRating<<=": thresh}
        )

    def submit_rating(self, track: PlexTrack, rating: int):
        """
        Submit a new track rating to the Plex server.
        """
        return track.rate(rating=rating)

    @staticmethod
    def parse_track_mbid(track: PlexTrack) -> Optional[str]:
        """
        Parses track MBID from a Plex track object
        """
        log.info(f"Trying to grab MBID from PlexTrack: {track.title}")
        try:
            mbid = track.guids[0].id
            mbid = mbid.removeprefix("mbid://")
            log.info(f"Found track ID from PlexTrack: {mbid}.")
        except IndexError:
            mbid = None
            log.warning("No track MBID found in PlexTrack.")

        return mbid
