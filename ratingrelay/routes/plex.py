import asyncio
from typing import Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.plex_auth import create_session, iter_sessions, remove_session
from ratingrelay.core.thread import run_sync
from ratingrelay.db import get_async_session
from ratingrelay.db.credentials import get_credential, upsert_credential
from ratingrelay.models.services import ServiceName
from ratingrelay.schemas.plex import (
    PlexAuthInitResponse,
    PlexSelectServerRequest,
    PlexServerItem,
    PlexServersResponse,
    PlexStatusResponse,
)
from ratingrelay.settings import get_settings, logger

settings = get_settings()

router = APIRouter(prefix="/api/plex", tags=["plex"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CALLBACK_URL = f"{settings.base_url.rstrip('/')}/api/plex/auth/callback"

# Minimal HTML page served inside the OAuth popup after Plex redirects back.
# Sends a postMessage to the opener and immediately closes itself.
_POPUP_CLOSE_HTML = """<!DOCTYPE html>
<html>
<head><title>Connecting…</title></head>
<body>
<script>
  if (window.opener) {{
    window.opener.postMessage('plex:authed', {target_origin!r});
  }}
  window.close();
</script>
<p>Authenticated. You can close this window.</p>
</body>
</html>""".format(target_origin=settings.base_url.rstrip("/"))


async def _get_token(db: AsyncSession) -> str | None:
    """Return the active Plex token: settings override first, then DB."""
    cred = await get_credential(db, ServiceName.plex)
    return settings.plex_token or (cred.token if cred else None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/auth/status", response_model=PlexStatusResponse)
async def plex_auth_status(
    db: AsyncSession = Depends(get_async_session),
) -> PlexStatusResponse:
    """
    Return the current Plex auth status.

    Settings token takes precedence over DB (dev override).
    """
    cred = await get_credential(db, ServiceName.plex)
    token = settings.plex_token or (cred.token if cred else None)

    if not token:
        return PlexStatusResponse(status="needs-auth")

    try:
        account = await run_sync(lambda: MyPlexAccount(token=token))
        return PlexStatusResponse(
            status="connected",
            username=account.username,
            server_name=cred.plex_server_name if cred else None,
            server_url=cred.plex_server_url if cred else None,
        )
    except Exception as exc:
        logger.error("Plex token validation failed: %s", exc)
        return PlexStatusResponse(status="needs-auth")


@router.post("/auth/init", response_model=PlexAuthInitResponse)
async def plex_auth_init() -> PlexAuthInitResponse:
    """
    Start a Plex OAuth PIN login flow.

    Creates a MyPlexPinLogin session, stores it in memory, and returns:
    - pin_id: integer used to correlate the callback
    - oauth_url: the plex.tv URL to open in a popup window
    """
    try:
        pin_login, oauth_url = await run_sync(lambda: create_session(CALLBACK_URL))
        pin_id = int(pin_login._id)
        logger.info("Plex OAuth init: pin_id=%s, callback=%s", pin_id, CALLBACK_URL)
        return PlexAuthInitResponse(pin_id=pin_id, oauth_url=oauth_url)
    except Exception as exc:
        logger.error("Failed to create Plex OAuth session: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to initiate Plex auth"
        ) from exc


@router.get("/auth/callback", response_class=HTMLResponse)
async def plex_auth_callback(
    db: AsyncSession = Depends(get_async_session),
) -> HTMLResponse:
    """
    OAuth callback — Plex redirects the popup here after the user logs in.

    Iterates over all active PIN sessions to find the one Plex has confirmed.
    Stores the token + username to the database, then serves a tiny HTML page
    that sends postMessage to the opener and closes the popup.
    """
    confirmed_token: str | None = None
    confirmed_pin_id: int | None = None

    for pin_id, pin_login in iter_sessions():
        try:
            ok = await run_sync(pin_login.checkLogin)
            if ok and pin_login.token:
                confirmed_token = pin_login.token
                confirmed_pin_id = pin_id
                break
        except Exception as exc:
            logger.debug("PIN %s not yet confirmed: %s", pin_id, exc)

    if not confirmed_token or confirmed_pin_id is None:
        logger.warning("Plex OAuth callback hit but no PIN confirmed yet")
        return HTMLResponse(_POPUP_CLOSE_HTML)

    remove_session(confirmed_pin_id)

    try:
        account = await run_sync(lambda: MyPlexAccount(token=confirmed_token))
        await upsert_credential(
            db,
            ServiceName.plex,
            token=confirmed_token,
            username=account.username,
        )
        logger.info("Plex authenticated as %s", account.username)
    except Exception as exc:
        logger.error("Failed to store Plex credentials: %s", exc)

    return HTMLResponse(_POPUP_CLOSE_HTML)


@router.get("/servers", response_model=PlexServersResponse)
async def plex_list_servers(
    db: AsyncSession = Depends(get_async_session),
) -> PlexServersResponse:
    """List Plex Media Servers accessible to the authenticated account."""
    token = await _get_token(db)
    if not token:
        raise HTTPException(status_code=401, detail="Plex not authenticated")

    try:
        account = await run_sync(lambda: MyPlexAccount(token=token))
        resources = await run_sync(
            lambda: [r for r in account.resources() if r.product == "Plex Media Server"]
        )
        return PlexServersResponse(
            servers=[
                PlexServerItem(
                    name=r.name,
                    product=r.product,
                    client_identifier=r.clientIdentifier,
                )
                for r in resources
            ]
        )
    except Exception as exc:
        logger.error("Failed to list Plex servers: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to list Plex servers"
        ) from exc


@router.post("/server", response_model=PlexStatusResponse)
async def plex_select_server(
    body: PlexSelectServerRequest,
    db: AsyncSession = Depends(get_async_session),
) -> PlexStatusResponse:
    """Connect to the named Plex server and store its URL."""
    cred = await get_credential(db, ServiceName.plex)
    token = settings.plex_token or (cred.token if cred else None)

    if not token:
        raise HTTPException(status_code=401, detail="Plex not authenticated")

    try:
        account = await run_sync(lambda: MyPlexAccount(token=token))
        server: PlexServer = await run_sync(
            lambda: account.resource(body.name).connect()
        )
    except Exception as exc:
        logger.error("Failed to connect to Plex server '%s': %s", body.name, exc)
        raise HTTPException(
            status_code=400,
            detail=f"Could not connect to server '{body.name}': {exc}",
        ) from exc

    cred = await upsert_credential(
        db,
        ServiceName.plex,
        token=token,
        username=cred.username if cred else None,
        plex_server_url=server._baseurl,
        plex_server_name=body.name,
    )

    return PlexStatusResponse(
        status="connected",
        username=cred.username,
        server_name=cred.plex_server_name,
        server_url=cred.plex_server_url,
    )
