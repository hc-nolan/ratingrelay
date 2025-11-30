from enum import Enum
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


class OperatingMode(str, Enum):
    relay = "relay"
    reset = "reset"


@lru_cache()
def get_settings():
    return Settings()


@lru_cache()
def set_mbz_user_agent(version: str):
    mbz.set_useragent(
        "RatingRelay", version, contact="https://github.com/hc-nolan/ratingrelay"
    )


class Settings(BaseSettings):
    version: str = "1.1"
    log_level: str = "INFO"
    timezone: str = "America/Toronto"
    mode: OperatingMode
    two_way: bool = False
    database: str
    love_threshold: int
    hate_threshold: Optional[int] = None
    plex_server_url: HttpUrl
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
            )

    model_config = SettingsConfigDict(env_file="config.env")


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
    LOG_FORMAT: str = "%(asctime)s:%(levelname)s:%(module)s:%(message)s"
    LOG_LEVEL: str = settings.log_level
    version: int = 1
    disable_existing_loggers: bool = False
    formatters: dict = {
        "default": {
            "format": LOG_FORMAT,
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    }
    handlers: dict = {
        "console": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "formatter": "default",
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": "data/ratingrelay.log",
            "when": "midnight",
            "interval": 30,
            "backupCount": 6,
        },
    }
    loggers: dict = {
        "ratingrelay": {
            "handlers": ["console", "file"],
            "level": LOG_LEVEL,
        },
        "musicbrainzngs": {
            "handlers": ["console", "file"],
            "level": "WARNING",
        },
        "pylast": {
            "handlers": ["console", "file"],
            "level": "WARNING",
        },
        "httpx": {
            "handlers": ["console", "file"],
            "level": "WARNING",
        },
    }


dictConfig(LogConfig().model_dump())
log = logging.getLogger("ratingrelay")
