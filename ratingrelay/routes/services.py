"""The accounts this relay holds credentials for.

One read path for the services page and the flow builder: what is connected, what
each account is called, and which one is the first. Adding, reconnecting and
removing an account stays on each service's own ``/api/{service}/auth`` route,
where the credential is validated by the service that owns it.

An account can come from two places. A stored row is what the web app owns. A
service configured **entirely from the environment** (``LISTENBRAINZ_TOKEN`` and
friends — how a Docker deploy wired to Key Vault is set up) has no row at all,
and is listed here anyway: the relay resolves those credentials from settings,
so an account the UI cannot see is an account it silently refuses to offer as a
source or a target.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.credentials import (
    hyperfix_credentials,
    lastfm_credentials,
    listenbrainz_credentials,
    plex_credentials,
    settings_describe,
)
from ratingrelay.db import get_async_session
from ratingrelay.db.credentials import list_accounts
from ratingrelay.engine.credentials import (
    HyperfixCredentials,
    LastFmCredentials,
    ListenBrainzCredentials,
    PlexCredentials,
)
from ratingrelay.engine.refs import KNOWN_SERVICES, default_label, is_primary
from ratingrelay.schemas.services import ServiceAccountItem, ServicesResponse

router = APIRouter(prefix="/api/services", tags=["services"])

# Asked only whether the values are complete, so nothing here makes a network
# call: a page load must not wait on four services. Each service's own
# ``/auth/status`` is where a token is actually verified.
_COMPLETE = {
    "plex": plex_credentials,
    "hyperfix": hyperfix_credentials,
    "lastfm": lastfm_credentials,
    "listenbrainz": listenbrainz_credentials,
}


def _from_settings(
    kind: str,
    resolved: PlexCredentials | LastFmCredentials | ListenBrainzCredentials | HyperfixCredentials,
) -> ServiceAccountItem:
    """The entry for an account the environment alone describes.

    It holds the bare kind as its ref — that is the ref settings speak for, and
    the one every run already writes under.
    """
    if isinstance(resolved, (LastFmCredentials, ListenBrainzCredentials)):
        username: str | None = resolved.username
        url: str | None = None
    elif isinstance(resolved, HyperfixCredentials):
        username, url = None, resolved.base_url
    else:
        username, url = None, resolved.server_url
    return ServiceAccountItem(
        id=kind,
        service=kind,
        label=default_label(kind),
        primary=True,
        configured=True,
        username=username,
        url=url,
        managed="settings",
    )


@router.get("", response_model=ServicesResponse)
async def list_services(
    db: AsyncSession = Depends(get_async_session),
) -> ServicesResponse:
    """Every connected account, the primary of each service first."""
    stored = await list_accounts(db)
    accounts = []
    for account in stored:
        try:
            await _COMPLETE[account.service.value](db, account.id)
            configured = True
        except ValueError:
            configured = False
        primary = is_primary(account.id)
        accounts.append(
            ServiceAccountItem(
                id=account.id,
                service=account.service.value,
                label=account.label,
                primary=primary,
                configured=configured,
                username=account.username,
                server_name=account.plex_server_name,
                url=account.base_url or account.plex_server_url,
                # A row is only the account's real source when the environment
                # does not shadow it: settings win field by field, for the bare
                # kind only, so a stored primary they fully describe is theirs.
                managed=(
                    "settings"
                    if primary and settings_describe(account.service.value)
                    else "database"
                ),
            )
        )

    # A service whose credentials are only in the environment has no row: list it
    # from settings, or the UI has nothing to draw while the relay happily runs.
    for kind in KNOWN_SERVICES:
        if any(account.id == kind for account in stored):
            continue
        resolver = _COMPLETE.get(kind)
        if resolver is None:  # a kind nothing can resolve yet: not connected
            continue
        try:
            resolved = await resolver(db, kind)
        except ValueError:
            continue
        accounts.append(_from_settings(kind, resolved))

    return ServicesResponse(accounts=accounts)
