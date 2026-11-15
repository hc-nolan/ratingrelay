from typing import Literal, Optional

from pydantic import BaseModel


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


class PlexSelectServerRequest(BaseModel):
    """Body for POST /api/plex/server."""

    name: str


class PlexStatusResponse(BaseModel):
    """Returned by GET /api/plex/auth/status."""

    status: Literal["connected", "needs-auth"]
    username: Optional[str] = None
    server_name: Optional[str] = None
    server_url: Optional[str] = None
