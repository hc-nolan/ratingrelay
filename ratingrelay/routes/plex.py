import asyncio
from typing import Callable, TypeVar
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
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

# Plex confirms the PIN moments after it redirects the popup back to us, so a
# single check races the confirmation. A few short retries here are what turn
# "nothing happened, try again" into a login that just works.
_CALLBACK_ATTEMPTS = 6
_CALLBACK_DELAY = 1.0

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _callback_url(request: Request) -> str:
    """Where Plex should send the popup back to.

    ``base_url`` is authoritative whenever it names a real address: that is the
    deployed case, and a configured answer must win. When it is still the
    localhost default, the browser is not necessarily on this machine — the dev
    server binds every interface, so the app can be open on a laptop elsewhere on
    the LAN. A localhost redirect would send that browser to its own localhost
    and the login would silently die, so fall back to the origin the request
    arrived on.
    """
    configured = settings.base_url.rstrip("/")
    if urlparse(configured).hostname not in _LOCAL_HOSTS:
        return f"{configured}/api/plex/auth/callback"
    return f"{str(request.base_url).rstrip('/')}/api/plex/auth/callback"

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

# Served when Plex never confirmed the PIN inside the retry window. It must not
# claim success: the opener waits for the status endpoint, not for this page.
_POPUP_PENDING_HTML = """<!DOCTYPE html>
<html>
<head><title>Not connected</title></head>
<body>
<p>Plex did not confirm the login. Close this window and try again.</p>
</body>
</html>"""


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
async def plex_auth_init(request: Request) -> PlexAuthInitResponse:
    """
    Start a Plex OAuth PIN login flow.

    Creates a MyPlexPinLogin session, stores it in memory, and returns:
    - pin_id: integer used to correlate the callback
    - oauth_url: the plex.tv URL to open in a popup window
    """
    callback_url = _callback_url(request)
    try:
        pin_login, oauth_url = await run_sync(lambda: create_session(callback_url))
        pin_id = int(pin_login._id)
        logger.info("Plex OAuth init: pin_id=%s, callback=%s", pin_id, callback_url)
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
    Plex confirms a moment after the redirect, so the check is retried briefly
    rather than racing it. Stores the token + username to the database, then
    serves a tiny HTML page that tells the opener and closes the popup.
    """
    confirmed_token: str | None = None
    confirmed_pin_id: int | None = None

    for attempt in range(_CALLBACK_ATTEMPTS):
        for pin_id, pin_login in iter_sessions():
            try:
                ok = await run_sync(pin_login.checkLogin)
                if ok and pin_login.token:
                    confirmed_token = pin_login.token
                    confirmed_pin_id = pin_id
                    break
            except Exception as exc:
                logger.debug("PIN %s not yet confirmed: %s", pin_id, exc)
        if confirmed_token:
            break
        if attempt + 1 < _CALLBACK_ATTEMPTS:
            await asyncio.sleep(_CALLBACK_DELAY)

    if not confirmed_token or confirmed_pin_id is None:
        logger.warning(
            "Plex OAuth callback hit but no PIN was confirmed in %ss",
            int(_CALLBACK_ATTEMPTS * _CALLBACK_DELAY),
        )
        return HTMLResponse(_POPUP_PENDING_HTML)

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
