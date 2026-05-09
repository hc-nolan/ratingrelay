from asyncio import get_event_loop
from functools import partial

from fastapi import APIRouter, Depends, HTTPException
from liblistenbrainz import ListenBrainz
from liblistenbrainz.errors import ListenBrainzAPIException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.db import get_async_session
from ratingrelay.models.services import ServiceCredential, ServiceName
from ratingrelay.schemas.listenbrainz import ListenBrainzAuthRequest, ListenBrainzStatusResponse
from ratingrelay.settings import get_settings, logger

settings = get_settings()

router = APIRouter(prefix="/api/listenbrainz", tags=["listenbrainz"])


async def _run_sync(fn):
    loop = get_event_loop()
    return await loop.run_in_executor(None, fn)


async def _get_credential(db: AsyncSession) -> ServiceCredential | None:
    result = await db.execute(
        select(ServiceCredential).where(ServiceCredential.service == ServiceName.listenbrainz)
    )
    return result.scalars().first()


def _validate_token(username: str, token: str) -> str:
    """Validate token by fetching listen count; returns username on success."""
    client = ListenBrainz()
    client.set_auth_token(token)
    # This will raise if the token is invalid
    client.get_user_listen_count(username)
    return username


@router.get("/auth/status", response_model=ListenBrainzStatusResponse)
async def listenbrainz_auth_status(
    db: AsyncSession = Depends(get_async_session),
) -> ListenBrainzStatusResponse:
    cred = await _get_credential(db)
    # settings values take precedence (dev override); fall back to DB
    token = settings.listenbrainz_token or (cred.token if cred else None)
    username = settings.listenbrainz_username or (cred.username if cred else None)

    if not token or not username:
        return ListenBrainzStatusResponse(status="needs-auth")

    try:
        await _run_sync(partial(_validate_token, username, token))
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
        await _run_sync(partial(_validate_token, body.username, body.token))
    except ListenBrainzAPIException as exc:
        logger.warning("ListenBrainz auth failed: %s", exc)
        raise HTTPException(status_code=401, detail=f"ListenBrainz authentication failed: {exc}")
    except Exception as exc:
        logger.error("ListenBrainz auth error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to authenticate with ListenBrainz")

    existing = await _get_credential(db)
    if existing:
        existing.token = body.token
        existing.username = body.username
        db.add(existing)
    else:
        db.add(ServiceCredential(
            service=ServiceName.listenbrainz,
            token=body.token,
            username=body.username,
        ))
    await db.commit()

    logger.info("ListenBrainz authenticated as %s", body.username)
    return ListenBrainzStatusResponse(status="connected", username=body.username)
