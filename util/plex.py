import logging
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer
from plexapi.library import LibrarySection
from plexapi.audio import Track as PlexTrack
from rich import print
from rich.prompt import IntPrompt, Prompt
from rapidfuzz import fuzz
from . import env
from .custom_types import Track

log = logging.getLogger(__name__)


class LibraryNotFoundError(Exception):
    """
    Exception class for cases where no matching
    music library is found on the Plex server
    """


class Plex:
    """
    Handles all interaction with Plex server
    :param `url`: URL for the Plex server
    :param `music_library`: Name of the music library
    :param `rating_threshold`: Integer representing the rating to consider tracks as 'loved'
    """  # noqa

    def __init__(self, url: str, music_library: str, rating_threshold: int):
        self.url = url
        self.rating_threshold = rating_threshold
        self.token = env.get_required("PLEX_TOKEN")
        self._verify_auth()
        self.music_library = self._get_music_library(music_library)

    def _verify_auth(self):
        """
        Checks if self.token is valid for authenticating to Plex. If token is not valid,
        proceeds with interactive authentication to retrieve a new token.
        """  # noqa: E501
        if not self.token:
            log.info(
                "No saved PLEX_TOKEN found. Proceeding with manual authentication."
            )
            self._manual_auth()
        if self._is_token_valid():
            log.info("Token is valid. Authenticated with Plex.")
        else:
            log.info("Saved PLEX_TOKEN is no longer valid. Please re-authenticate.")
            self._manual_auth()

    def _manual_auth(self):
        """
        Handles the manual authentication process. Retrieves the valid token after
        authentication for future use.
        """  # noqa: E501
        print(
            "Please enter your Plex authentication details. "
            "This should only be required the first time the program is run."
        )

        plex_username = Prompt.ask("Plex username")
        plex_password = Prompt.ask("Plex password (input hidden)", password=True)
        plex_server = Prompt.ask("Plex server name")
        plex_code = IntPrompt.ask(
            "Plex MFA code (leave blank if not using MFA)", default=0
        )

        account = MyPlexAccount(
            username=plex_username, password=plex_password, code=plex_code
        )
        plex = account.resource(plex_server).connect()
        self.server = plex

        self.token = plex._token
        env.write_var("PLEX_TOKEN", self.token)

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

    def get_tracks(self) -> list[PlexTrack]:
        """
        Queries a given library for all tracks meeting the `RATING_THRESHOLD` defined in `.env`
        """  # noqa: E501
        return self.music_library.search(
            libtype="track", userRating=self.rating_threshold
        )

    def get_track(self, track: Track) -> list[PlexTrack]:
        """
        Queries the Plex library for a track that matches the provided
        Track, which has attributes `artist` and `title`
        :param track: A Track tuple with attributes `artist` and `title`
        :
        """
        search = self.music_library.search(title=track.title, libtype="track")
        matches = []
        if search:
            for result in search:
                # make sure title is an exact match, because
                # the Plex search returns partial matches
                if result.title == track.title:
                    plex_artist = result.artist().title
                    if similar_enough(plex_artist, track.artist):
                        matches.append(result)
        return matches

    def submit_rating(self, track: PlexTrack, rating: int):
        """
        Submit a new track rating to the Plex server.
        """
        return track.rate(rating=rating)


def similar_enough(a: str, b: str) -> bool:
    """
    Uses rapidfuzz.fuzz to compare strings `a` and `b`. If their similarity is
    above a 0.7 ratio, they are 'similar enough'
    :returns `True` if the similarity ratio between `a` and `b` is >= 0.7
    """
    return fuzz.ratio(a, b) >= 0.7
