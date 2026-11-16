from functools import partial
from typing import Optional

import pylast
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.credentials import lastfm_credentials
from ratingrelay.core.thread import run_sync
from ratingrelay.db import get_async_session
from ratingrelay.db.credentials import delete_credential, upsert_credential
from ratingrelay.engine.refs import account_ref
from ratingrelay.schemas.lastfm import LastFMAuthRequest, LastFMStatusResponse
from ratingrelay.settings import logger

router = APIRouter(prefix="/api/lastfm", tags=["lastfm"])


@router.get("/auth/status", response_model=LastFMStatusResponse)
async def lastfm_auth_status(
    account: Optional[str] = None,
    db: AsyncSession = Depends(get_async_session),
) -> LastFMStatusResponse:
    """Is this Last.fm account connected?

    ``account`` names a second one; leaving it out asks about the account that
    already existed. Credential precedence (settings, then the stored row) lives
    in ``core.credentials``, which pins settings to the first account — an
    environment variable cannot name a second one.
    """
    try:
        credentials = await lastfm_credentials(db, account_ref("lastfm", account))
    except ValueError:
        return LastFMStatusResponse(status="needs-auth")

    try:
        network = await run_sync(
            partial(
                pylast.LastFMNetwork,
                api_key=credentials.api_key,
                api_secret=credentials.api_secret,
                username=credentials.username,
                password_hash=credentials.password_hash,
            )
        )
        user = await run_sync(network.get_authenticated_user)
        resolved_username = await run_sync(user.get_name)
        return LastFMStatusResponse(status="connected", username=resolved_username)
    except pylast.WSError as exc:
        logger.warning("Last.fm token validation failed: %s", exc)
        return LastFMStatusResponse(status="needs-auth")
    except Exception as exc:
        logger.error("Last.fm status check error: %s", exc)
        return LastFMStatusResponse(status="needs-auth")


@router.post("/auth", response_model=LastFMStatusResponse)
async def lastfm_auth(
    body: LastFMAuthRequest,
    db: AsyncSession = Depends(get_async_session),
) -> LastFMStatusResponse:
    """Store a validated Last.fm credential, for one account.

    A second account (``account`` set) gets its own ref and its own history; the
    first one keeps the bare kind, so everything the relay has already written
    stays attached to it.
    """
    password_hash = pylast.md5(body.password)

    try:
        network = await run_sync(
            partial(
                pylast.LastFMNetwork,
                api_key=body.api_key,
                api_secret=body.api_secret,
                username=body.username,
                password_hash=password_hash,
            )
        )
        user = await run_sync(network.get_authenticated_user)
        username = await run_sync(user.get_name)
    except pylast.WSError as exc:
        logger.warning("Last.fm auth failed: %s", exc)
        raise HTTPException(
            status_code=401, detail=f"Last.fm authentication failed: {exc}"
        ) from exc
    except Exception as exc:
        logger.error("Last.fm auth error: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to authenticate with Last.fm"
        ) from exc

    fields = {
        "token": body.api_key,
        "secret": body.api_secret,
        "username": username,
        "password_hash": password_hash,
    }
    if body.label:
        fields["label"] = body.label
    ref = account_ref("lastfm", body.account)
    await upsert_credential(db, ref, **fields)
    logger.info("Last.fm authenticated as %s (%s)", username, ref)
    return LastFMStatusResponse(status="connected", username=username)


@router.delete("/auth", response_model=LastFMStatusResponse)
async def lastfm_disconnect(
    account: Optional[str] = None,
    db: AsyncSession = Depends(get_async_session),
) -> LastFMStatusResponse:
    """Forget one Last.fm account's credentials.

    Sync records are deliberately kept: they are what the relay wrote, and a
    reconnected account has to be able to undo those rather than rediscover them
    as if they were the user's own. Another account of Last.fm is untouched.
    """
    ref = account_ref("lastfm", account)
    if await delete_credential(db, ref):
        logger.info("Last.fm credentials removed (%s)", ref)
    return await lastfm_auth_status(account=account, db=db)
