"""Resolve service credentials for the engine.

Precedence is settings, then the stored credential row — the dev-override
pattern the project keeps. The result is a plain value object; nothing below
this layer knows where a token came from.

Every function takes an account *ref* (``plex``, ``lastfm:old``; see
``engine.refs``). Settings describe **one** account — the one whose ref is the
bare kind — because an environment variable cannot name a second one. A second
account is configured in the web app and read from its row alone.
"""

from typing import Optional

import pylast
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.db.credentials import get_credential
from ratingrelay.engine.credentials import (
    HyperfixCredentials,
    LastFmCredentials,
    ListenBrainzCredentials,
    PlexCredentials,
)
from ratingrelay.engine.refs import is_primary
from ratingrelay.settings import get_settings

# The settings that together describe one account, per kind. Kept beside the
# resolvers below, which read the same fields in the same order: a new credential
# field belongs in both places or the two answers disagree.
_SETTING_FIELDS = {
    "plex": ("plex_token", "plex_server_url"),
    "lastfm": ("lastfm_token", "lastfm_secret", "lastfm_username", "lastfm_password"),
    "listenbrainz": ("listenbrainz_token", "listenbrainz_username"),
    "hyperfix": ("hyperfix_api_key", "hyperfix_url"),
}


def settings_describe(kind: str) -> bool:
    """Whether the environment alone fully describes the first account of *kind*.

    Only the bare kind is a ref settings can speak for. The services list asks
    this so it can say where an account's credentials really come from: a row the
    environment shadows still resolves from settings, and offering to replace or
    remove it would be a button that cannot take effect.
    """
    settings = get_settings()
    return all(getattr(settings, field) for field in _SETTING_FIELDS[kind])


async def plex_credentials(db: AsyncSession, service: str = "plex") -> PlexCredentials:
    settings = get_settings()
    cred = await get_credential(db, service)
    primary = is_primary(service)
    token = (settings.plex_token if primary else None) or (cred.token if cred else None)
    server_url = (
        str(settings.plex_server_url).rstrip("/")
        if primary and settings.plex_server_url
        else (cred.plex_server_url if cred else None)
    )
    if not token or not server_url:
        raise ValueError(f"Plex not configured ({service})")
    return PlexCredentials(
        server_url=server_url, token=token, library=settings.plex_music_library
    )


async def lastfm_credentials(
    db: AsyncSession, service: str = "lastfm"
) -> LastFmCredentials:
    settings = get_settings()
    cred = await get_credential(db, service)
    primary = is_primary(service)
    api_key = (settings.lastfm_token if primary else None) or (
        cred.token if cred else None
    )
    api_secret = (settings.lastfm_secret if primary else None) or (
        cred.secret if cred else None
    )
    username = (settings.lastfm_username if primary else None) or (
        cred.username if cred else None
    )
    password_hash: Optional[str] = (
        pylast.md5(settings.lastfm_password)
        if primary and settings.lastfm_password
        else None
    ) or (cred.password_hash if cred else None)
    if not api_key or not api_secret or not username or not password_hash:
        raise ValueError(f"Last.fm not configured ({service})")
    return LastFmCredentials(
        api_key=api_key,
        api_secret=api_secret,
        username=username,
        password_hash=password_hash,
    )


async def listenbrainz_credentials(
    db: AsyncSession, service: str = "listenbrainz"
) -> ListenBrainzCredentials:
    settings = get_settings()
    cred = await get_credential(db, service)
    primary = is_primary(service)
    token = (settings.listenbrainz_token if primary else None) or (
        cred.token if cred else None
    )
    username = (settings.listenbrainz_username if primary else None) or (
        cred.username if cred else None
    )
    if not token or not username:
        raise ValueError(f"ListenBrainz not configured ({service})")
    return ListenBrainzCredentials(token=token, username=username)


async def hyperfix_credentials(
    db: AsyncSession, service: str = "hyperfix"
) -> HyperfixCredentials:
    """A hyperfix instance and the key to its ratings feed.

    Settings describe the first account only, as everywhere else; a second one is read
    from its row. The URL lives on the row too, because a bridge instance is an address
    rather than a well-known host.
    """
    settings = get_settings()
    cred = await get_credential(db, service)
    primary = is_primary(service)
    token = (settings.hyperfix_api_key if primary else None) or (
        cred.token if cred else None
    )
    base_url = (
        str(settings.hyperfix_url).rstrip("/")
        if primary and settings.hyperfix_url
        else (cred.base_url if cred else None)
    )
    if not token or not base_url:
        raise ValueError(f"Hyperfix not configured ({service})")
    return HyperfixCredentials(base_url=base_url, token=token)
