from functools import partial

from fastapi import APIRouter, Depends, HTTPException
from liblistenbrainz import ListenBrainz
from liblistenbrainz.errors import ListenBrainzAPIException
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.thread import run_sync
from ratingrelay.db import get_async_session
from ratingrelay.db.credentials import get_credential, upsert_credential
from ratingrelay.models.services import ServiceName
from ratingrelay.schemas.listenbrainz import (
    ListenBrainzAuthRequest,
    ListenBrainzStatusResponse,
)
from ratingrelay.settings import get_settings, logger

settings = get_settings()

router = APIRouter(prefix="/api/listenbrainz", tags=["listenbrainz"])


def _validate_token(username: str, token: str) -> str:
    """Validate a ListenBrainz token via the /1/validate-token endpoint.

    Returns the username on success; raises ListenBrainzAPIException on failure.
    The validate-token endpoint requires the auth token and confirms it is valid
    for the given user, unlike public endpoints (e.g. get_user_listen_count)
    which accept any token.
    """
    client = ListenBrainz()
    client.set_auth_token(token)
    result = client._get("/1/validate-token")
    if not result.get("valid"):
        raise ListenBrainzAPIException(
            status_code=401, message=result.get("message", "Invalid token")
        )
    return username


@router.get("/auth/status", response_model=ListenBrainzStatusResponse)
async def listenbrainz_auth_status(
    db: AsyncSession = Depends(get_async_session),
) -> ListenBrainzStatusResponse:
    cred = await get_credential(db, ServiceName.listenbrainz)
    # Settings values take precedence (dev override); fall back to DB
    token = settings.listenbrainz_token or (cred.token if cred else None)
    username = settings.listenbrainz_username or (cred.username if cred else None)

    if not token or not username:
        return ListenBrainzStatusResponse(status="needs-auth")

    try:
        await run_sync(partial(_validate_token, username, token))
        return ListenBrainzStatusResponse(status="connected", username=username)
    except Exception as exc:
        logger.warning("ListenBrainz token validation failed: %s", exc)
        return ListenBrainzStatusResponse(status="needs-auth")


@router.post("/auth", response_model=ListenBrainzStatusResponse)
async def listenbrainz_auth(
    body: ListenBrainzAuthRequest,
    db: AsyncSession = Depends(get_async_session),
) -> ListenBrainzStatusResponse:
    try:
        await run_sync(partial(_validate_token, body.username, body.token))
    except ListenBrainzAPIException as exc:
        logger.warning("ListenBrainz auth failed: %s", exc)
        raise HTTPException(
            status_code=401, detail=f"ListenBrainz authentication failed: {exc}"
        )
    except Exception as exc:
        logger.error("ListenBrainz auth error: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to authenticate with ListenBrainz"
        )

    await upsert_credential(
        db,
        ServiceName.listenbrainz,
        token=body.token,
        username=body.username,
    )
    logger.info("ListenBrainz authenticated as %s", body.username)
    return ListenBrainzStatusResponse(status="connected", username=body.username)
