"""Resolve service credentials for the engine.

Precedence is settings, then the stored credential row — the dev-override
pattern the project keeps. The result is a plain value object; nothing below
this layer knows where a token came from.
"""

from typing import Optional

import pylast
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.db.credentials import get_credential
from ratingrelay.engine.credentials import (
    LastFmCredentials,
    ListenBrainzCredentials,
    PlexCredentials,
)
from ratingrelay.models.services import ServiceName
from ratingrelay.settings import get_settings


async def plex_credentials(db: AsyncSession) -> PlexCredentials:
    settings = get_settings()
    cred = await get_credential(db, ServiceName.plex)
    token = settings.plex_token or (cred.token if cred else None)
    server_url = (
        str(settings.plex_server_url).rstrip("/")
        if settings.plex_server_url
        else (cred.plex_server_url if cred else None)
    )
    if not token or not server_url:
        raise ValueError("Plex not configured")
    return PlexCredentials(
        server_url=server_url, token=token, library=settings.plex_music_library
    )


async def lastfm_credentials(db: AsyncSession) -> LastFmCredentials:
    settings = get_settings()
    cred = await get_credential(db, ServiceName.lastfm)
    api_key = settings.lastfm_token or (cred.token if cred else None)
    api_secret = settings.lastfm_secret or (cred.secret if cred else None)
    username = settings.lastfm_username or (cred.username if cred else None)
    password_hash: Optional[str] = (
        pylast.md5(settings.lastfm_password) if settings.lastfm_password else None
    ) or (cred.password_hash if cred else None)
    if not api_key or not api_secret or not username or not password_hash:
        raise ValueError("Last.fm not configured")
    return LastFmCredentials(
        api_key=api_key,
        api_secret=api_secret,
        username=username,
        password_hash=password_hash,
    )


async def listenbrainz_credentials(db: AsyncSession) -> ListenBrainzCredentials:
    settings = get_settings()
    cred = await get_credential(db, ServiceName.listenbrainz)
    token = settings.listenbrainz_token or (cred.token if cred else None)
    username = settings.listenbrainz_username or (cred.username if cred else None)
    if not token or not username:
        raise ValueError("ListenBrainz not configured")
    return ListenBrainzCredentials(token=token, username=username)
