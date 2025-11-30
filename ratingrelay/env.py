from typing import Optional
from pathlib import Path
from os import getenv
import logging

from .exceptions import ConfigError

log = logging.getLogger("ratingrelay")


class Env:
    """
    Contains functions related to interacting with the .env file.

    Mostly wrappers around os.getenv()
    """

    @staticmethod
    def write_var(name: str, value: str) -> None:
        """
        Writes or updates an environment variable
        """
        log.info(f"Writing new {name} to config.env.")
        env_file = Env.get_env_file()

        with open(env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        updated = False
        for i, line in enumerate(lines):
            if line.startswith(name):
                lines[i] = name + "=" + value + "\n"
                updated = True

        if not updated:
            log.info(f"No saved {name} found. Adding it now.")
            # If above did not produce an update,
            # it means no line '<NAME>=' was found; append it
            lines.append("\n" + name + "=" + value + "\n")
            updated = True

        if updated:
            with open(env_file, "w", encoding="utf-8") as f:
                f.writelines(lines)
            log.info(f"Updated saved {name} value.")
        else:
            raise IOError(
                f"Unable to write to env file. Cannot continue without {name}.\n"
                f"Please manually add it: {value}"
            )

    @staticmethod
    def get_env_file() -> Path:
        """
        Retrieve the path of the application's .env file
        """
        env_file = Path(__file__).parent.parent / "config.env"
        if env_file.exists():
            log.info(f"Found .env file at: {env_file}")
            return env_file
        else:
            raise FileNotFoundError(
                ".env file not found in repository root directory. "
                "Check the usage guide for details on "
                "setting up the environment variables."
            )

    @staticmethod
    def get_required(var_name: str) -> str:
        """
        Wraps os.getenv() - raises an exception if value is not present
        """
        value: Optional[str] = getenv(var_name)
        if not value:
            raise ConfigError(
                f"Environment variable {var_name} is not set. "
                "Please add it and re-run the script."
            )
        return value

    @staticmethod
    def get_required_int(var_name: str) -> int:
        """
        Wraps get_required() - raises an exception if an integer
        value is not present
        """
        value = Env.get_required(var_name)
        return int(value)

    @staticmethod
    def get_required_bool(var_name: str) -> bool:
        """
        Wraps get_required() - raises an exception if a boolean value
        is not present
        """
        value = Env.get_required(var_name)
        return bool(value)

    @staticmethod
    def get(var_name: str) -> Optional[str]:
        """
        Simple wrapper for os.getenv()
        """
        return getenv(var_name)
