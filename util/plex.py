import time
import logging
import json
import pathlib
from urllib.parse import urlencode
import requests
import xmltodict
from typing import Optional
from uuid import uuid4

log = logging.getLogger(__name__)

class LibraryNotFoundError(Exception):
    """
    Exception class for cases where no matching
    music library is found on the Plex server
    """


class Plex:
    """
    Class for all Plex-related operations
    """
    def __init__(
        self,
        threshold: str,
        library: str,
        url: str,
        cid: Optional[str],
        token: Optional[str],
    ):
        self.app_name = "ratingrelay"
        self.threshold = threshold
        self.url = url
        if not cid:
            cid = str(uuid4())
            self._write_env_var("CID", cid)
        self.cid = cid
        self.token = self._auth(token)
        self.library = self._get_music_library(library)

    def _auth(self, token: str | None) -> str:
        """
        Handles full authentication process
        """
        if token is None:
            return self._new_auth()
        # Check for existing token
        valid = self._check_token_validity(token)
        if not valid:
            return self._new_auth()

        return token

    def _new_auth(self) -> str:
        """
        Initial auth process
        Ref: https://forums.plex.tv/t/authenticating-with-plex/609370
        """
        # Generate PIN
        resp = requests.post(
            url="https://plex.tv/api/v2/pins",
            data={
                "strong": "true",
                "X-Plex-Product": self.app_name,
                "X-Plex-Client-Identifier": self.cid,
            },
            headers={"accept": "application/json"},
            timeout=30
        )
        content = json.loads(resp.content)
        # Grab PIN ID and code
        pin_id = content["id"]
        pin_code = content["code"]

        # Construct auth URL; user has to open in browser
        params = {
            "clientID": self.cid,
            "code": pin_code,
            "context[device][product]": self.app_name,
        }
        url = "https://app.plex.tv/auth#?" + urlencode(params)
        log.info("Please open the below URL in a web browser to authenticate to Plex.")
        log.info("Plex auth URL: %s", url)

        # Poll the ID each second to determine if user has authed
        auth = None
        while auth is None:
            resp = requests.get(
                url=f"https://plex.tv/api/v2/pins/{pin_id}",
                headers={"accept": "application/json"},
                data={"code": pin_code, "X-Plex-Client-Identifier": self.cid},
                timeout=30
            )
            content = json.loads(resp.content)
            if content["authToken"] is not None:
                auth = content["authToken"]
                self._write_env_var("TOKEN", auth)
            # User has not completed auth flow; Sleep for 1s and retry
            time.sleep(1)
        return auth

    def _check_token_validity(self, token_to_check: str) -> bool:
        """
        Check if Plex API token is still valid
        """
        resp = requests.get(
            url="https://plex.tv/api/v2/user",
            headers={"accept": "application/json"},
            data={

                "X-Plex-Product": self.app_name,
                "X-Plex-Client-Identifier": self.cid,
                "X-Plex-Token": token_to_check,
            },
            timeout=30
        )
        if resp.status_code == 200:
            return True
        return False

    @staticmethod
    def _write_env_var(name: str, value: str) -> None:
        """
        Writes or updates an environment variable
        """
        env_file = pathlib.Path(__file__).parent.parent / ".env"
        log.info("%s", env_file)
        with open(env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        updated = False
        for i, line in enumerate(lines):
            if line.startswith(name):
                lines[i] = name + "=" + value
                updated = True

        if not updated:
            # If above did not produce an update, it means no line 'TOKEN=' was found; append it
            lines.append(name + "=" + value)
            updated = True

        if updated:
            with open(env_file, "w", encoding="utf-8") as f:
                f.writelines(lines)
        else:
            raise IOError(
                f"Unable to write to env file. Cannot continue without {name}.\n"
                f"Please manually add it: {value}"
            )


    def _get_music_library(self, library_name: str) -> str:
        """
        Searches for music library matching value of MUSIC_LIBRARY .env variable
        :return: The Plex music library key, if found
        """
        libraries_resp = requests.get(
            url=f"{self.url}/library/sections",
            params={"X-Plex-Token": self.token},
            timeout=30
        )
        libraries_resp = xmltodict.parse(libraries_resp.content)
        libraries = libraries_resp["MediaContainer"]["Directory"]
        for lib in libraries:
            if lib["@title"] == library_name:
                return lib["@key"]

        libraries_found = [lib["@title"] for lib in libraries]
        raise LibraryNotFoundError(
            f"No library named '{library_name}' found on Plex Server. "
            f"Please ensure this matches the library name exactly. "
            f"Libraries found: {libraries_found}"
        )

    def get_tracks(self) -> list:
        """
        Queries a given library for all tracks meeting the RATING_THRESHOLD defined in .env
        :param library_key: Key for the Music library to query; returned by get_music_library()
        :param auth_token: X-Plex-Token; returned by Plex.auth()
        :return: List of all tracks meeting the rating threshold
        """
        url = f"{self.url}/library/sections/{self.library}/all"
        params = {
            "X-Plex-Token": self.token,
            "type": 10,
            "userRating>": self.threshold
        }
        r = requests.get(
            url=url,
            params=params,
            timeout=30
        )
        response_dict = xmltodict.parse(r.content)
        return trim_tracks(response_dict["MediaContainer"]["Track"])



def trim_tracks(track_list: list) -> list:
    """"
    Filters list of track dictionaries returned by Plex; retains only artist, title, label, and year
    """
    # Convert each item to frozenset for hashability so we can get unique tracks
    unique_tracks = set()
    for track in track_list:
        try:
            label = track["@parentStudio"]
        except KeyError:
            label = None
        try:
            year = track["@parentYear"]
        except KeyError:
            year = None
        track_items = frozenset({
            "title": track["@title"],
            "artist": track["@grandparentTitle"],
            "label": label,
            "year": year
        }.items())
        unique_tracks.add(track_items)
    # Convert back to list
    return [dict(track) for track in unique_tracks]


