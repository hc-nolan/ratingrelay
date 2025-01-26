import time
import requests
from plex_api_client import PlexAPI
from os import getenv
from dotenv import load_dotenv
import json
from urllib.parse import urlencode

load_dotenv()
CID = getenv("CID")
if CID is None or CID == "":
    raise ValueError(
        "Client identifier not found. Please generate a random uuid and add it to .env"
    )
SERVER_URL = getenv("SERVER_URL")
if SERVER_URL is None or SERVER_URL == "":
    raise ValueError(
        "Server URL not found. Please add it to .env. Format: http(s)://ip.or.domain:port"
    )
MUSIC_LIBRARY = getenv("MUSIC_LIBRARY")
if MUSIC_LIBRARY is None or MUSIC_LIBRARY == "":
    raise ValueError("Music library name not found. Please add it to .env")


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

if __name__ == "__main__":
    plex_auth = PlexAuth()
    token = plex_auth.auth()
    with PlexAPI(
        server_url=SERVER_URL,
        access_token=token,
    ) as plex_api:
        res = plex_api.library.get_all_libraries()
        assert res.object is not None

        libraries = res.object.media_container.directory
        plex_music_lib = None
        for lib in libraries:
            if lib.title == MUSIC_LIBRARY:
                plex_music_lib = lib
        if plex_music_lib is None:
            raise LibraryNotFoundError(f"No library named {MUSIC_LIBRARY} found. Please ensure this matches the library name exactly. Found libraries: {[lib.title for lib in libraries]}")
        print(plex_music_lib)

