from typing import Literal, Optional

from pydantic import BaseModel

from ratingrelay.schemas.services import AccountFields


class PlexAuthInitResponse(BaseModel):
    """Returned by POST /api/plex/auth/init."""

    pin_id: int
    oauth_url: str


class PlexServerItem(BaseModel):
    """A single Plex Media Server resource the account has access to."""

    name: str
    product: str  # e.g. "Plex Media Server"
    client_identifier: str


class PlexServersResponse(BaseModel):
    servers: list[PlexServerItem]


class PlexSelectServerRequest(AccountFields):
    """Body for POST /api/plex/server.

    ``account`` names a second server of the same Plex account (``plex:attic``);
    leaving it out switches the primary one. A second *Plex account* would be a
    second identity, which this app does not have.
    """

    name: str


class PlexStatusResponse(BaseModel):
    """Returned by GET /api/plex/auth/status."""

    status: Literal["connected", "needs-auth"]
    username: Optional[str] = None
    server_name: Optional[str] = None
    server_url: Optional[str] = None
