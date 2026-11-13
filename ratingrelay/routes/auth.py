"""Sign in as the Plex account the relay syncs as.

There is one user. RatingRelay stores no passwords: it asks Plex to confirm a
PIN, checks that the resulting token belongs to the same account as the one
already configured, and remembers the answer in a signed cookie. Overseerr and
Tautulli work the same way, and it means a household that already trusts Plex
does not have to trust another password store.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.plex_auth import (
    create_session,
    get_session,
    public_url,
    remove_session,
)
from ratingrelay.core.session import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    identify,
    issue_session,
    owner_identity,
    owner_token,
    verify_session,
)
from ratingrelay.core.thread import run_sync
from ratingrelay.db import get_async_session
from ratingrelay.schemas.auth import AuthStatusResponse, LoginInitResponse, LoginPollResponse
from ratingrelay.settings import get_settings, logger

settings = get_settings()

router = APIRouter(prefix="/api/auth", tags=["auth"])

_POPUP_CLOSE_HTML = """<!DOCTYPE html>
<html>
<head><title>Signed in</title></head>
<body>
<script>window.close();</script>
<p>Signed in. You can close this window.</p>
</body>
</html>"""


def _set_session_cookie(
    response: Response, request: Request, username_id: str, key_token: str
) -> None:
    secure = request.url.scheme == "https"
    response.set_cookie(
        SESSION_COOKIE,
        issue_session(username_id, key_token),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )
    logger.info(
        "Session cookie issued for Plex user %s (secure=%s, path=/, host=%s)",
        username_id,
        secure,
        request.headers.get("host", "?"),
    )


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(
    request: Request,
    db: AsyncSession = Depends(get_async_session),
) -> Response:
    """Is a login needed, and does this browser already have one?"""
    cookie = request.cookies.get(SESSION_COOKIE)
    owner = await owner_identity(db)
    if owner is None:
        # Nothing configured yet: the setup wizard has to be reachable.
        return _status(AuthStatusResponse(required=False, authenticated=False, cookie=bool(cookie)))
    user_id, username = owner
    if not settings.auth:
        return _status(
            AuthStatusResponse(
                required=False, authenticated=True, username=username, cookie=bool(cookie)
            )
        )
    token = await owner_token(db)
    signed_in = bool(token) and verify_session(cookie, token) == user_id
    return _status(
        AuthStatusResponse(
            required=True,
            authenticated=signed_in,
            username=username,
            cookie=bool(cookie),
        )
    )


def _status(body: AuthStatusResponse) -> Response:
    """A login state is never cacheable: a cached "no" outlives the login."""
    payload = JSONResponse(body.model_dump())
    payload.headers["Cache-Control"] = "no-store, must-revalidate"
    payload.headers["Pragma"] = "no-cache"
    return payload


@router.post("/plex/init", response_model=LoginInitResponse)
async def login_init(request: Request) -> LoginInitResponse:
    """Open a Plex PIN the browser can confirm in a popup."""
    forward_url = public_url(
        settings.base_url, str(request.base_url), "/api/auth/plex/done"
    )
    try:
        pin_login, auth_url = await run_sync(lambda: create_session(forward_url))
    except Exception as exc:  # noqa: BLE001 - reported as a 500 to the UI
        logger.error("Failed to create Plex login session: %s", exc)
        raise HTTPException(status_code=502, detail="Plex did not answer") from exc
    return LoginInitResponse(pin_id=int(pin_login._id), auth_url=auth_url)


@router.get("/plex/poll", response_model=LoginPollResponse)
async def login_poll(
    request: Request,
    pin_id: int,
    db: AsyncSession = Depends(get_async_session),
) -> Response:
    """Wait for the PIN to be confirmed, then check *who* confirmed it.

    Only the owner's Plex account may hold a session. Anyone else who can reach
    this relay can start a login, but it ends at a 403.
    """
    pin_login = get_session(pin_id)
    if pin_login is None:
        raise HTTPException(status_code=404, detail="Login expired")

    try:
        confirmed = await run_sync(pin_login.checkLogin)
    except Exception as exc:  # noqa: BLE001 - "not yet" arrives as an exception
        logger.debug("PIN %s not confirmed yet: %s", pin_id, exc)
        confirmed = False

    if not confirmed or not pin_login.token:
        return JSONResponse(LoginPollResponse(status="pending").model_dump())

    token = pin_login.token
    owner = await owner_identity(db)
    if owner is None:
        # No owner configured: setup owns this flow, this endpoint cannot.
        remove_session(pin_id)
        raise HTTPException(status_code=409, detail="Relay is not configured yet")

    user_id, owner_name = owner
    who = await identify(token)
    if who is None or who[0] != user_id:
        remove_session(pin_id)
        logger.warning(
            "Plex login refused: account %s is not the relay's owner",
            who[0] if who else "unknown",
        )
        raise HTTPException(status_code=403, detail="This relay is not yours")

    remove_session(pin_id)
    payload = JSONResponse(
        LoginPollResponse(status="ok", username=owner_name).model_dump()
    )
    # Sign with the *configured* credential's token, not the one this PIN just
    # minted. Plex hands out a new token per login, and the guard can only verify
    # against the token the relay actually holds — keying on the PIN token
    # produces a cookie that never verifies, i.e. a login that silently fails.
    key_token = await owner_token(db)
    if not key_token:
        raise HTTPException(status_code=409, detail="Relay has no Plex token")
    _set_session_cookie(payload, request, user_id, key_token)
    logger.info("Signed in as %s", owner_name)
    return payload


@router.get("/plex/done", response_class=HTMLResponse)
async def login_done() -> HTMLResponse:
    """Where Plex sends the popup once the user confirms. Nothing else to do."""
    return HTMLResponse(_POPUP_CLOSE_HTML)


@router.post("/logout")
async def logout() -> Response:
    payload = JSONResponse({"ok": True})
    payload.delete_cookie(SESSION_COOKIE, path="/")
    return payload
