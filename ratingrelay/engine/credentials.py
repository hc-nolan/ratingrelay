"""Credential value types the service clients need.

Resolving them (settings over environment over database) belongs to the caller:
the engine is handed a credential or nothing, and reports the latter as
``NOT_CONFIGURED``.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlexCredentials:
    """A Plex server plus the library inside it to read ratings from."""

    server_url: str
    token: str
    library: str


@dataclass(frozen=True, slots=True)
class LastFmCredentials:
    """Last.fm needs a signed API session, not just a token."""

    api_key: str
    api_secret: str
    username: str
    password_hash: str


@dataclass(frozen=True, slots=True)
class ListenBrainzCredentials:
    """ListenBrainz submits feedback with a user token; the username scopes reads."""

    token: str
    username: str
