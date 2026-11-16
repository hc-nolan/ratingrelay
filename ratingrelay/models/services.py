from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class ServiceName(str, Enum):
    plex = "plex"
    lastfm = "lastfm"
    listenbrainz = "listenbrainz"


class ServiceCredential(SQLModel, table=True):
    """
    Stores authentication credentials for a connected service.

    One row per service; upserted on (re-)authentication.
    """

    service: ServiceName = Field(primary_key=True)
    token: str = Field()
    username: Optional[str] = Field(default=None)

    # Used for services that require a secret alongside the token (e.g. Last.fm API secret)
    secret: Optional[str] = Field(default=None)

    # Stored as an MD5 hash for pylast authentication
    password_hash: Optional[str] = Field(default=None)

    # Plex-specific: populated after the user picks a server
    plex_server_url: Optional[str] = Field(default=None)
    plex_server_name: Optional[str] = Field(default=None)

    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
