"""The accounts this relay holds credentials for.

One read path for the services page and the flow builder: what is connected, what
each account is called, and which one is the first. Adding, reconnecting and
removing an account stays on each service's own ``/api/{service}/auth`` route,
where the credential is validated by the service that owns it.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.credentials import (
    lastfm_credentials,
    listenbrainz_credentials,
    plex_credentials,
)
from ratingrelay.db import get_async_session
from ratingrelay.db.credentials import list_accounts
from ratingrelay.engine.refs import is_primary
from ratingrelay.schemas.services import ServiceAccountItem, ServicesResponse

router = APIRouter(prefix="/api/services", tags=["services"])

# Asked only whether the stored values are complete, so this stays a database
# read: a page load must not make four network calls to four services.
_COMPLETE = {
    "plex": plex_credentials,
    "lastfm": lastfm_credentials,
    "listenbrainz": listenbrainz_credentials,
}


@router.get("", response_model=ServicesResponse)
async def list_services(
    db: AsyncSession = Depends(get_async_session),
) -> ServicesResponse:
    """Every connected account, the primary of each service first."""
    accounts = []
    for account in await list_accounts(db):
        try:
            await _COMPLETE[account.service.value](db, account.id)
            configured = True
        except ValueError:
            configured = False
        accounts.append(
            ServiceAccountItem(
                id=account.id,
                service=account.service.value,
                label=account.label,
                primary=is_primary(account.id),
                configured=configured,
                username=account.username,
                server_name=account.plex_server_name,
            )
        )
    return ServicesResponse(accounts=accounts)
