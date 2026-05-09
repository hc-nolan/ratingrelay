import sys
from functools import lru_cache
from typing import Optional
import logging
from pydantic import HttpUrl
from pydantic_settings import SettingsConfigDict, BaseSettings
from pydantic_core import ValidationError
import musicbrainzngs as mbz


class Settings(BaseSettings):
    """
    Application settings

    Attributes:
        model_config: Change the value of `env_file` to use a custom environment variable file.
        frontend_dir: Directory that the Svelte frontend builds to
        base_url: URL to be used for requests to the application
        database: Path to the database file
        log_level: Logging level
        timezone: Timezone in IANA format
        secret: Secret to use for authentication. Should be a securely generated 64+ char string.
        test_limit: Request limit used when running tests
    """

    version: str = "2.0.0"
    model_config = SettingsConfigDict(env_file=".config")

    frontend_dir: str = "frontend/build"
    base_url: str = "http://localhost:8000"
    database: str = "ratingrelay.db"
    log_level: str = "INFO"
    timezone: str = "America/Toronto"
    secret: str = "AUTHSECRET"
    test_limit: Optional[int] = 10
    # Below settings are all optional. If not found, you will be prompted
    # to enter them in the frontend.
    # Values here take precedence over values in the database.
    plex_server_url: Optional[HttpUrl] = None
    plex_music_library: str = "Music"
    plex_token: Optional[str] = None
    lastfm_token: Optional[str] = None
    lastfm_secret: Optional[str] = None
    lastfm_username: Optional[str] = None
    lastfm_password: Optional[str] = None
    listenbrainz_token: Optional[str] = None
    listenbrainz_username: Optional[str] = None


@lru_cache()
def get_settings() -> Settings:
    """
    Returns the app settings/configuration.
    """
    return Settings()


@lru_cache()
def set_mbz_user_agent(version: str):
    """
    Set user agent for MusicBrainz
    """
    mbz.set_useragent(
        "RatingRelay", version, contact="https://github.com/hc-nolan/ratingrelay"
    )


try:
    settings = get_settings()
    set_mbz_user_agent(settings.version)
except ValidationError as e:
    logging.basicConfig(level=logging.ERROR)
    logging.fatal(
        "Invalid settings. Please inspect the below error and edit your config.env file."
    )
    logging.fatal(e)
    sys.exit(1)


LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
            "use_colors": None,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
        "ratingrelay": {
            "handlers": ["default"],
            "level": settings.log_level,
            "propagate": False,
        },
    },
}

logger = logging.getLogger("ratingrelay")
