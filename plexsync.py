import time
import requests
from os import getenv
from dotenv import load_dotenv
import json
from urllib.parse import urlencode
import xmltodict
from uuid import uuid4
import pylast


def generate_uuid():
    """
    Generates a random UUID and writes it to .env CID variable
    Called during startup if no existing CID is found
    """
    uuid = str(uuid4())

    with open(".env", "r") as f:
        lines = f.readlines()

    updated = False
    for i, line in enumerate(lines):
        if line.startswith("CID"):
            lines[i] = "CID=" + uuid
            updated = True

    if not updated:
        # If above did not produce an update, it means no line 'CID=' was found; append it
        # First, add newline to the last line of env file
        lines[-1] += "\n"
        lines.append("CID=" + uuid)
        updated = True

    if updated:
        with open(".env", "w") as f:
            f.writelines(lines)
        print("CID saved to .env file.")
    else:
        raise ValueError("CID was not updated.")



load_dotenv()
CID = getenv("CID")
if CID is None or CID == "":
    generate_uuid()
SERVER_URL = getenv("SERVER_URL")
if SERVER_URL is None or SERVER_URL == "":
    raise ValueError(
        "Server URL not found. Please add it to .env. Format: http(s)://ip.or.domain:port"
    )
MUSIC_LIBRARY = getenv("MUSIC_LIBRARY")
if MUSIC_LIBRARY is None or MUSIC_LIBRARY == "":
    raise ValueError("Music library name not found. Please add it to .env")
RATING_THRESHOLD = getenv("RATING_THRESHOLD")
if RATING_THRESHOLD is None or RATING_THRESHOLD == "":
    raise ValueError("Rating threshold not found. Please add it to .env")


class PlexAuth:
    def __init__(self):
        self.APP_NAME = "PlexLists"

    def auth(self) -> str:
        """
        Handles full authentication process
        """
        print("Authenticating with Plex...")
        # Check for existing token
        existing_token = getenv("TOKEN")
        if existing_token:
            valid = self.check_token_validity(existing_token)
            if not valid:
                print("Stored Plex API token has expired. Please reauthenticate.")
                return self.new_auth()
            else:
                print("Stored Plex API token is still valid.\n")
                return existing_token
        else:
            print("No stored Plex API token found.")
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
                "X-Plex-Product": self.APP_NAME,
                "X-Plex-Client-Identifier": CID,
            },
            headers={"accept": "application/json"},
        )
        content = json.loads(resp.content)
        # Grab PIN ID and code
        pin_id = content["id"]
        pin_code = content["code"]

        # Construct auth URL; user has to open in browser
        params = {
            "clientID": CID,
            "code": pin_code,
            "context[device][product]": self.APP_NAME,
        }
        url = "https://app.plex.tv/auth#?" + urlencode(params)
        print("To sign in, please open the below URL in a web browser:")
        print(url)

        # Poll the ID each second to determine if user has authed
        auth = None
        while auth is None:
            resp = requests.get(
                url=f"https://plex.tv/api/v2/pins/{pin_id}",
                headers={"accept": "application/json"},
                data={"code": pin_code, "X-Plex-Client-Identifier": CID},
            )
            content = json.loads(resp.content)
            if content["authToken"] is not None:
                print("Authentication succeeded!")
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
                "X-Plex-Product": self.APP_NAME,
                "X-Plex-Client-Identifier": CID,
                "X-Plex-Token": token_to_check,
            },
        )
        if resp.status_code == 200:
            return True
        return False

    @staticmethod
    def write_env_token(token_to_write: str) -> None:
        """
        Writes a valid Plex API token to .env file's TOKEN variable
        """
        with open(".env", "r") as f:
            lines = f.readlines()

        updated = False
        for i, line in enumerate(lines):
            if line.startswith("TOKEN"):
                lines[i] = "TOKEN=" + token_to_write
                updated = True

        if not updated:
            # If above did not produce an update, it means no line 'TOKEN=' was found; append it
            lines.append("TOKEN=" + token_to_write)
            updated = True

        if updated:
            with open(".env", "w") as f:
                f.writelines(lines)
            print("Token saved to .env file.")
        else:
            raise ValueError("Token was not updated.")


class LibraryNotFoundError(Exception):
    pass


def get_music_library(auth_token) -> str:
    # Searches for music library matching value of MUSIC_LIBRARY .env variable
    libraries_resp = requests.get(
        url=f"{SERVER_URL}/library/sections",
        params={"X-Plex-Token": auth_token}
    )
    libraries_resp = xmltodict.parse(libraries_resp.content)
    libraries = libraries_resp["MediaContainer"]["Directory"]
    plex_music_lib = None
    for lib in libraries:
        if lib["@title"] == MUSIC_LIBRARY:
            plex_music_lib = lib
            return plex_music_lib["@key"]
    if plex_music_lib is None:
        raise LibraryNotFoundError(
            f"No library named {MUSIC_LIBRARY} found. Please ensure this matches the library name exactly. Found libraries: {[lib.title for lib in libraries]}")


def get_tracks(library_key, auth_token) -> list:
    """
    Queries a given library for all tracks meeting the RATING_THRESHOLD defined in .env
    :param library_key: Key for the Music library to query; returned by get_music_library()
    :param auth_token: X-Plex-Token; returned by PlexAuth.auth()
    :return: List of all tracks meeting the rating threshold
    """
    url = f"{SERVER_URL}/library/sections/{library_key}/all"
    params = {
        "X-Plex-Token": auth_token,
        "type": 10,
        "userRating>": RATING_THRESHOLD
    }
    r = requests.get(url=url, params=params)
    response_dict = xmltodict.parse(r.content)
    return response_dict["MediaContainer"]["Track"]


def lastfm_connect() -> pylast.LastFMNetwork | None:
    """
    Creates a connection to Last.fm using pylast
    :return: None if any environment variables are missing; the LastFMNetwork instance otherwise
    """
    key = getenv("LASTFM_API_KEY")
    secret = getenv("LASTFM_SECRET")
    username = getenv("LASTFM_USERNAME")
    password = getenv("LASTFM_PASSWORD")

    if not all(val is not None or val != "" for val in (key, secret, username, password)):
        print("SKIPPING LAST.FM: One or more Last.fm environment variables are missing.")
        print("If you intended to use Last.fm, make sure all environment variables are set.")
        return

    return pylast.LastFMNetwork(
        api_key=key,
        api_secret=secret,
        username=username,
        password_hash=pylast.md5(password)
    )


def lastfm_love(network: pylast.LastFMNetwork, tracks_to_love: list):
    """
    Iterates through tracks returned from Plex and submits them as Loved Tracks to Last.fm
    :param network: LastFMNetwork returned from lastfm_connect()
    :param tracks_to_love: List of tracks meeting the defined RATING_THRESHOLD returned by get_tracks()
    """
    for track in tracks_to_love:
        track_title = track["@title"]
        track_artist = track["@grandparentTitle"]
        lastfm_track = network.get_track(track_artist, track_title)
        lastfm_track.love()



if __name__ == "__main__":
    plex_auth = PlexAuth()
    token = plex_auth.auth()
    print("Querying Plex for tracks meeting the rating threshold.")
    library = get_music_library(token)
    tracks = get_tracks(library, token)
    print(f"Found {len(tracks)} meeting the rating threshold.")

    lastfm = lastfm_connect()
    lastfm_love(network=lastfm, tracks_to_love=tracks)
