"""
ratingrelay - a script to sync Plex tracks rated above a certain threshold
to external services like Last.fm and ListenBrainz
"""
import logging
import sys
import time
import json
from uuid import uuid4
from os import getenv
from urllib.parse import urlencode
import requests
from dotenv import load_dotenv
import xmltodict
import pylast


def generate_uuid():
    """
    Generates a random UUID and writes it to .env CID variable
    Called during startup if no existing CID is found
    """
    uuid = str(uuid4())

    with open(".env", "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated = False
    for i, line in enumerate(lines):
        if line.startswith("CID"):
            lines[i] = "CID=" + uuid + "\n"
            updated = True

    if not updated:
        # If above did not produce an update, it means no line 'CID=' was found; append it
        # First, add newline to the last line of env file
        lines[-1] += "\n"
        lines.append("CID=" + uuid)
        updated = True

    if updated:
        with open(".env", "w", encoding="utf-8") as f:
            f.writelines(lines)
        log.info("Saved new CID to .env file.")
    else:
        log.error(
            "Unable to write a new CID to .env file. "
            "Please generate a random uuid4 and add it manually."
        )
        sys.exit(1)


log = logging.getLogger(__name__)

load_dotenv()
CID = getenv("CID")
MISSING = False
if CID is None or CID == "":
    log.warning("No CID found in .env file; generating and writing a new one.")
    generate_uuid()

SERVER_URL = getenv("SERVER_URL")
if SERVER_URL is None or SERVER_URL == "":
    log.error(
        "Server URL not found. Please add it to .env. Format: http(s)://ip.or.domain:port"
    )
    MISSING = True

MUSIC_LIBRARY = getenv("MUSIC_LIBRARY")
if MUSIC_LIBRARY is None or MUSIC_LIBRARY == "":
    log.error("Music library name not found. Please add it to .env")
    MISSING = True

RATING_THRESHOLD = getenv("RATING_THRESHOLD")
if RATING_THRESHOLD is None or RATING_THRESHOLD == "":
    log.error("Rating threshold not found. Please add it to .env")
    MISSING = True

if MISSING:
    sys.exit(1)


class LibraryNotFoundError(Exception):
    """
    Exception class for cases where no matching
    music library is found on the Plex server
    """


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    plex = Plex()
    token = plex.auth()
    log.info("Querying Plex for tracks meeting the rating threshold.")
    try:
        library = plex.get_music_library(token)
    except LibraryNotFoundError as e:
        log.fatal(str(e))
        sys.exit(1)
    tracks = plex.get_tracks(library, token)
    tracks = trim_tracks(tracks)
    log.info("Found %s tracks meeting rating threshold.", len(tracks))

    lastfm = LastFM.connect()
    LastFM.love(network=lastfm, tracks_to_love=tracks)


def trim_tracks(track_list: list) -> list:
    """"
    Filters list of track dictionaries returned by Plex; retains only artist, title, label, and year
    """
    print(len(track_list))
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


class Plex:
    """
    Class for all Plex-related operations
    """
    def __init__(self):
        self.app_name = "ratingrelay"

    def auth(self) -> str:
        """
        Handles full authentication process
        """
        log.info("Authenticating with Plex.")
        # Check for existing token
        existing_token = getenv("PLEX_TOKEN")
        if existing_token:
            valid = self.check_token_validity(existing_token)
            if not valid:
                log.warning("Stored Plex API token has expired. Please reauthenticate.")
                return self.new_auth()

            log.info("Stored Plex API token is still valid.\n")
            return existing_token

        log.warning("No stored Plex API token found.")
        return self.new_auth()

    def new_auth(self) -> str:
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
                "X-Plex-Client-Identifier": CID,
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
            "clientID": CID,
            "code": pin_code,
            "context[device][product]": self.app_name,
        }
        url = "https://app.plex.tv/auth#?" + urlencode(params)
        log.info("To sign in, please open the below URL in a web browser:")
        log.info(url)

        # Poll the ID each second to determine if user has authed
        auth = None
        while auth is None:
            resp = requests.get(
                url=f"https://plex.tv/api/v2/pins/{pin_id}",
                headers={"accept": "application/json"},
                data={"code": pin_code, "X-Plex-Client-Identifier": CID},
                timeout=30
            )
            content = json.loads(resp.content)
            if content["authToken"] is not None:
                log.info("Authentication succeeded.")
                auth = content["authToken"]
                self.write_env_token(auth)
            # User has not completed auth flow; Sleep for 1s and retry
            time.sleep(1)
        return auth

    def check_token_validity(self, token_to_check: str) -> bool:
        """
        Check if Plex API token is still valid
        """
        resp = requests.get(
            url="https://plex.tv/api/v2/user",
            headers={"accept": "application/json"},
            data={

                "X-Plex-Product": self.app_name,
                "X-Plex-Client-Identifier": CID,
                "X-Plex-Token": token_to_check,
            },
            timeout=30
        )
        if resp.status_code == 200:
            return True
        return False

    @staticmethod
    def write_env_token(token_to_write: str) -> None:
        """
        Writes a valid Plex API token to .env file's PLEX_TOKEN variable
        """
        with open(".env", "r", encoding="utf-8") as f:
            lines = f.readlines()

        updated = False
        for i, line in enumerate(lines):
            if line.startswith("PLEX_TOKEN"):
                lines[i] = "PLEX_TOKEN=" + token_to_write
                updated = True

        if not updated:
            # If above did not produce an update, it means no line 'TOKEN=' was found; append it
            lines.append("PLEX_TOKEN=" + token_to_write)
            updated = True

        if updated:
            with open(".env", "w", encoding="utf-8") as f:
                f.writelines(lines)
            log.info("Token saved to .env file.")
        else:
            log.fatal(
                "Unable to write Plex API token to file. Cannot continue without Plex API token.\n"
                "Please manually add it:\tPLEX_TOKEN=%s", token_to_write
            )
            sys.exit(1)

    @staticmethod
    def get_music_library(auth_token: str) -> str:
        """
        Searches for music library matching value of MUSIC_LIBRARY .env variable
        :param auth_token: Plex API token
        :return: The Plex music library, if found
        """
        libraries_resp = requests.get(
            url=f"{SERVER_URL}/library/sections",
            params={"X-Plex-Token": auth_token},
            timeout=30
        )
        libraries_resp = xmltodict.parse(libraries_resp.content)
        libraries = libraries_resp["MediaContainer"]["Directory"]
        for lib in libraries:
            if lib["@title"] == MUSIC_LIBRARY:
                return lib["@key"]

        libraries_found = [lib["@title"] for lib in libraries]
        raise LibraryNotFoundError(
            f"No library named '{MUSIC_LIBRARY}' found on Plex Server. "
            f"Please ensure this matches the library name exactly. "
            f"Libraries found: {libraries_found}"
        )

    @staticmethod
    def get_tracks(library_key, auth_token) -> list:
        """
        Queries a given library for all tracks meeting the RATING_THRESHOLD defined in .env
        :param library_key: Key for the Music library to query; returned by get_music_library()
        :param auth_token: X-Plex-Token; returned by Plex.auth()
        :return: List of all tracks meeting the rating threshold
        """
        url = f"{SERVER_URL}/library/sections/{library_key}/all"
        params = {
            "X-Plex-Token": auth_token,
            "type": 10,
            "userRating>": RATING_THRESHOLD
        }
        r = requests.get(
            url=url,
            params=params,
            timeout=30
        )
        response_dict = xmltodict.parse(r.content)
        return response_dict["MediaContainer"]["Track"]


class LastFM:
    """
    Class for all LastFM-related operations
    """
    connected = False

    @classmethod
    def connect(cls) -> pylast.LastFMNetwork | None:
        """
        Creates a connection to Last.fm using pylast
        :return: None if any environment variables are missing; the LastFMNetwork instance otherwise
        """
        key = getenv("LASTFM_API_KEY")
        secret = getenv("LASTFM_SECRET")
        username = getenv("LASTFM_USERNAME")
        password = getenv("LASTFM_PASSWORD")

        if not all(val is not None or val != "" for val in (key, secret, username, password)):
            log.warning(
                "SKIPPING LAST.FM: One or more Last.fm environment variables are missing.\n"
                "If you intended to use Last.fm, make sure all environment variables are set."
            )
            return None

        cls.connected = True
        return pylast.LastFMNetwork(
            api_key=key,
            api_secret=secret,
            username=username,
            password_hash=pylast.md5(password)
        )

    @classmethod
    def love(cls, network: pylast.LastFMNetwork, tracks_to_love: list):
        """
        Iterates through tracks returned from Plex and submits them as Loved Tracks to Last.fm
        :param network: LastFMNetwork returned from lastfm_connect()
        :param tracks_to_love: List of tracks meeting the defined
                                RATING_THRESHOLD returned by get_tracks()
        """
        if not cls.connected:
            log.warning("Not connected to Last.fm - no tracks will be loved.")
            return

        for track in tracks_to_love:
            track_title = track["title"]
            track_artist = track["artist"]
            lastfm_track = network.get_track(track_artist, track_title)
            lastfm_track.love()
            log.info("Last.FM -- Loved: %s - %s", track_artist, track_title)


if __name__ == "__main__":
    main()
