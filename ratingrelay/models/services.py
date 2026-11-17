from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class ServiceName(str, Enum):
    plex = "plex"
    lastfm = "lastfm"
    listenbrainz = "listenbrainz"
    hyperfix = "hyperfix"


class ServiceAccount(SQLModel, table=True):
    """One connected account of one service.

    ``id`` is the account's **ref** — ``lastfm``, ``lastfm:old`` — and is what
    every sync record, mapping, unmatched row and job config keys on (see
    ``engine.refs``). The account that already existed keeps the bare kind as its
    ref permanently, which is what lets a second account be added without
    rewriting the history of the first.

    It is immutable. Renaming a ref would strand every sync record pointing at it
    and every ``relays.toml`` on disk, which the app cannot rewrite; the display
    name is ``label``.

    A removed account is **soft-deleted** (``deleted_at``) so its ref is never
    reissued: a new account wearing an old ref would inherit writes it never
    made, and could not tell which of them were its own to undo.
    """

    id: str = Field(primary_key=True)  # the ref: "lastfm" or "lastfm:old"
    service: ServiceName = Field(index=True)
    label: str = Field(default="")

    token: str = Field()
    username: Optional[str] = Field(default=None)

    # Used for services that require a secret alongside the token (e.g. Last.fm API secret)
    secret: Optional[str] = Field(default=None)

    # Stored as an MD5 hash for pylast authentication
    password_hash: Optional[str] = Field(default=None)

    # The address of an account that is a URL rather than a well-known service
    # (hyperfix); Plex keeps its own column because it also stores a library name.
    base_url: Optional[str] = Field(default=None)

    # Plex-specific: populated after the user picks a server
    plex_server_url: Optional[str] = Field(default=None)
    plex_server_name: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    # Removed accounts stay so their ref — and the history written under it — is
    # never handed to a different account.
    deleted_at: Optional[datetime] = Field(default=None)
