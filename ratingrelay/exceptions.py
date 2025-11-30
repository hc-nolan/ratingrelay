class ConfigError(Exception):
    """Raised when required configuration value is missing or invalid"""


class LibraryNotFoundError(Exception):
    """Raised when a matching music library is not found on Plex server"""
