"""

This file contains all functions related to interacting with the .env file.
"""

import logging
from typing import Optional
from dotenv import load_dotenv
from os import getenv
from pathlib import Path

log = logging.getLogger(__name__)


def write_var(name: str, value: str) -> None:
    """
    Writes or updates an environment variable
    """
    log.info("Writing new %s to .env file.", name)
    env_file = get_env_file()

    with open(env_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated = False
    for i, line in enumerate(lines):
        if line.startswith(name):
            lines[i] = name + "=" + value + "\n"
            updated = True

    if not updated:
        log.info("No saved %s found. Adding it now.", name)
        # make sure current last line ends with \n, otherwise
        # would be written to the same line
        if len(lines) > 0 and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        lines.append(name + "=" + value + "\n")
        updated = True

    if updated:
        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(lines)
        log.info("Updated saved %s value.", name)
    else:
        raise IOError(
            f"Unable to write to env file. Cannot continue without {name}.\n"
            f"Please manually add it: {value}"
        )


def get_env_file() -> Path:
    """
    Retrieve the path of the application's .env file
    """
    env_file = Path(__file__).parent.parent.parent / ".env"
    if env_file.exists():
        log.info("Found .env file at: %s", env_file)
        return env_file
    else:
        raise FileNotFoundError(
            ".env file not found in repository root directory. "
            "Check the usage guide for details on "
            "setting up the environment variables."
        )


def get_required(var_name: str) -> str:
    """
    Wraps os.getenv() - raises an exception if value is not present
    """
    value: Optional[str] = getenv(var_name)
    if not value:
        raise ValueError(
            f"Environment variable {var_name} is not set. "
            "Please add it and re-run the script."
        )
    return value


def get_required_int(var_name: str) -> int:
    """
    Wraps get_required() - raises an exception if an integer value is not present
    """  # noqa:E501
    value = get_required(var_name)
    return int(value)


def get_required_bool(var_name: str) -> bool:
    """
    Wraps get_required() - raises an exception if a boolean value is not present
    """  # noqa:E501
    value = get_required(var_name)
    return bool(value)


def get(var_name: str) -> Optional[str]:
    """
    Simple wrapper for os.getenv()
    """
    return getenv(var_name)


env_file = get_env_file()
load_dotenv(env_file)
