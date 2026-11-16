from functools import partial
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from liblistenbrainz import ListenBrainz
from liblistenbrainz.errors import ListenBrainzAPIException
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.credentials import listenbrainz_credentials
from ratingrelay.core.thread import run_sync
from ratingrelay.db import get_async_session
from ratingrelay.db.credentials import delete_credential, upsert_credential
from ratingrelay.engine.refs import account_ref
from ratingrelay.schemas.listenbrainz import (
    ListenBrainzAuthRequest,
    ListenBrainzStatusResponse,
)
from ratingrelay.settings import logger

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
    account: Optional[str] = None,
    db: AsyncSession = Depends(get_async_session),
) -> ListenBrainzStatusResponse:
    """Is this ListenBrainz account connected?

    ``account`` names a second one; leaving it out asks about the account that
    already existed. Precedence (settings, then the stored row) lives in
    ``core.credentials``, which pins settings to the first account.
    """
    try:
        credentials = await listenbrainz_credentials(
            db, account_ref("listenbrainz", account)
        )
    except ValueError:
        return ListenBrainzStatusResponse(status="needs-auth")
    return ListenBrainzStatusResponse(status="connected", username=credentials.username)


@router.post("/auth", response_model=ListenBrainzStatusResponse)
async def listenbrainz_auth(
    body: ListenBrainzAuthRequest,
    db: AsyncSession = Depends(get_async_session),
) -> ListenBrainzStatusResponse:
    """Store a validated ListenBrainz token, for one account."""
    try:
        await run_sync(partial(_validate_token, body.username, body.token))
    except ListenBrainzAPIException as exc:
        logger.warning("ListenBrainz auth failed: %s", exc)
        raise HTTPException(
            status_code=401, detail=f"ListenBrainz authentication failed: {exc}"
        ) from exc
    except Exception as exc:
        logger.error("ListenBrainz auth error: %s", exc)
        raise HTTPException(
            status_code=500, detail="Failed to authenticate with ListenBrainz"
        ) from exc

    fields = {"token": body.token, "username": body.username}
    if body.label:
        fields["label"] = body.label
    ref = account_ref("listenbrainz", body.account)
    await upsert_credential(db, ref, **fields)
    logger.info("ListenBrainz authenticated as %s (%s)", body.username, ref)
    return ListenBrainzStatusResponse(status="connected", username=body.username)


@router.delete("/auth", response_model=ListenBrainzStatusResponse)
async def listenbrainz_disconnect(
    account: Optional[str] = None,
    db: AsyncSession = Depends(get_async_session),
) -> ListenBrainzStatusResponse:
    """Forget one ListenBrainz account's credentials; sync records stay."""
    ref = account_ref("listenbrainz", account)
    if await delete_credential(db, ref):
        logger.info("ListenBrainz credentials removed (%s)", ref)
    return await listenbrainz_auth_status(account=account, db=db)
