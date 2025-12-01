import os
import time
from typing import Optional

from rich.prompt import Prompt

from .config import settings, log, Settings
from .plex import Plex
from .lastfm import LastFM
from .listenbrainz import ListenBrainz
from .database import Database
from .exceptions import ConfigError
from .reset import reset
from .services import Services
from .relay import relay


def setup_lastfm(config: Settings) -> Optional[LastFM]:
    """
    Set up Last.fm service if credentials are provided.
    """
    if not all(
        [
            config.lastfm_username,
            config.lastfm_password,
            config.lastfm_token,
            config.lastfm_secret,
        ]
    ):
        log.info("Last.fm credentials not provided - skipping Last.fm")
        return None

    try:
        lfm = LastFM(config)

        return lfm
    except ConfigError as e:
        log.warning("Failed to configure Last.fm - skipping Last.fm")
        log.warning(f"Error details: {e}")
        log.warning("This can be safely ignored if you do not wish to use Last.fm")
        return None


def setup_listenbrainz(config: Settings) -> Optional[ListenBrainz]:
    """
    Set up ListenBrainz service if credentials are provided.
    """
    if not all([config.listenbrainz_username, config.listenbrainz_token]):
        log.info("ListenBrainz credentials not provided - skipping ListenBrainz")
        return None

    try:
        return ListenBrainz(config)
    except ConfigError as e:
        log.error("Failed to configure ListenBrainz - skipping ListenBrainz")
        log.error(f"Error details: {e}")
        log.error("This can be safely ignored if you do not wish to use ListenBrainz")
        return None


def setup_services(config: Settings) -> Services:
    """
    Sets up all services: Plex, database, Last.fm, and ListenBrainz.
    Optional services (Last.fm/ListenBrainz) will be None if credentials aren't provided.
    """
    db = Database(config)
    plex = Plex(config)
    lfm = setup_lastfm(config)
    lbz = setup_listenbrainz(config)

    return Services(plex=plex, db=db, lfm=lfm, lbz=lbz)


def main():
    """main"""
    start_time = time.perf_counter()

    os.makedirs("data", exist_ok=True)  # ensure data directory exists

    settings_json = settings.model_dump_json(indent=2)
    log.info(f"Configured settings:\n{settings_json}")

    services = setup_services(settings)
    match settings.mode:
        case "relay":
            relay(services=services, settings=settings)
        case "reset":
            reset_user_check = Prompt.ask(
                "Reset mode will reset all loved/hated tracks on ListenBrainz "
                "and/or LastFM. This cannot be undone. To continue, enter 'reset'"
            )
            if reset_user_check == "reset":
                reset(services)
            else:
                log.info(
                    f"Answer '{reset_user_check}' does not equal 'reset' - exiting."
                )

    exec_time = time.perf_counter() - start_time
    log.info(f"RatingRelay finished in {exec_time:2f} seconds.")
