import sys
from typing import Optional
from functools import lru_cache
from logging.config import dictConfig
import logging
from pydantic import BaseModel, HttpUrl, field_validator
from pydantic_settings import SettingsConfigDict, BaseSettings
from pydantic_core import ValidationError
import httpx
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

    version = "2.0.0"
    model_config = SettingsConfigDict(env_file=".config")

    frontend_dir: str = "frontend/build"
    base_url: str = "http://localhost:8000"
    database: str = "ratingrelay.db"
    log_level: str = "INFO"
    timezone: str = "America/Toronto"
    secret: str = "AUTHSECRET"
    test_limit: Optional[int] = 10
    # TODO: frontend flow for all of these if not provided at startup
    plex_server_url: Optional[HttpUrl] = None
    plex_music_library: str = "Music"
    plex_token: Optional[str] = None
    lastfm_token: Optional[str] = None
    lastfm_secret: Optional[str] = None
    lastfm_username: Optional[str] = None
    lastfm_password: Optional[str] = None
    listenbrainz_token: Optional[str] = None
    listenbrainz_username: Optional[str] = None

    @field_validator("plex_server_url")
    @classmethod
    def validate_server_reachable(cls, v):
        """Check if the Plex server is reachable."""
        try:
            httpx.head(str(v), timeout=5.0, follow_redirects=True)
            return v
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise ValueError(
                f"Cannot reach Plex server at {v}. "
                f"Please check the URL and ensure the server is running. Error: {e}"
            ) from e

    # END TODO


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


class LogConfig(BaseModel):
    LOGGER_NAME: str = "ratingrelay"
    LOG_FORMAT: str = "%(levelprefix)s %(asctime)s %(message)s"
    LOG_LEVEL: str = settings.log_level
    version: int = 1
    formatters: dict = {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": LOG_FORMAT,
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    }
    handlers: dict = {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
    }
    loggers: dict = {
        "uvicorn": {"handlers": ["default"], "level": LOG_LEVEL},
        "ratingrelay": {"handlers": ["default"], "level": LOG_LEVEL},
    }


dictConfig(LogConfig().model_dump())
logger = logging.getLogger("ratingrelay")
