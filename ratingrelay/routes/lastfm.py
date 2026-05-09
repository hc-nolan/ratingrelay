from functools import partial

import pylast
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.thread import run_sync
from ratingrelay.db import get_async_session
from ratingrelay.db.credentials import get_credential, upsert_credential
from ratingrelay.models.services import ServiceName
from ratingrelay.schemas.lastfm import LastFMAuthRequest, LastFMStatusResponse
from ratingrelay.settings import get_settings, logger

settings = get_settings()

router = APIRouter(prefix="/api/lastfm", tags=["lastfm"])


@router.get("/auth/status", response_model=LastFMStatusResponse)
async def lastfm_auth_status(
    db: AsyncSession = Depends(get_async_session),
) -> LastFMStatusResponse:
    cred = await get_credential(db, ServiceName.lastfm)
    # Settings values take precedence (dev override); fall back to DB
    api_key = settings.lastfm_token or (cred.token if cred else None)
    api_secret = settings.lastfm_secret or (cred.secret if cred else None)
    username = settings.lastfm_username or (cred.username if cred else None)
    password = settings.lastfm_password  # only in settings; DB stores hash
    password_hash = (pylast.md5(password) if password else None) or (
        cred.password_hash if cred else None
    )

    if not api_key or not api_secret or not username or not password_hash:
        return LastFMStatusResponse(status="needs-auth")

    try:
        network = await run_sync(
            partial(
                pylast.LastFMNetwork,
                api_key=api_key,
                api_secret=api_secret,
                username=username,
                password_hash=password_hash,
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
        )
    except Exception as exc:
        logger.error("Last.fm auth error: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to authenticate with Last.fm"
        )

    await upsert_credential(
        db,
        ServiceName.lastfm,
        token=body.api_key,
        secret=body.api_secret,
        username=username,
        password_hash=password_hash,
    )
    logger.info("Last.fm authenticated as %s", username)
    return LastFMStatusResponse(status="connected", username=username)
