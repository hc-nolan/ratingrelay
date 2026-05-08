from functools import lru_cache
from logging.config import dictConfig
import logging
from pydantic import BaseModel
from pydantic_settings import SettingsConfigDict, BaseSettings


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
    """

    model_config = SettingsConfigDict(env_file=".config")

    frontend_dir: str = "frontend/build"
    base_url: str = "http://localhost:8000"
    database: str = "ratingrelay.db"
    log_level: str = "INFO"
    timezone: str = "America/Toronto"
    secret: str = "AUTHSECRET"


@lru_cache()
def get_settings() -> Settings:
    """
    Returns the app settings/configuration.
    """
    return Settings()


settings = get_settings()


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
