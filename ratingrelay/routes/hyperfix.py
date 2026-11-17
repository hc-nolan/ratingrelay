"""Connecting a hyperfix instance (HF-182).

A hyperfix source reads ``GET /api/music/ratings`` with a bearer key. Connecting is
therefore a URL and a key, validated by asking that same feed for one row: the key is
proved against the endpoint the relay will actually use, which no well-known auth dance
can stand in for.
"""

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.credentials import hyperfix_credentials
from ratingrelay.db import get_async_session
from ratingrelay.db.credentials import delete_credential, upsert_credential
from ratingrelay.engine.refs import account_ref
from ratingrelay.schemas.hyperfix import HyperfixAuthRequest, HyperfixStatusResponse
from ratingrelay.settings import logger

router = APIRouter(prefix="/api/hyperfix", tags=["hyperfix"])

FEED_PATH = "/api/music/ratings"
PROBE_TIMEOUT = 20.0


def _probe(url: str, token: str) -> None:
    """Fail unless the feed answers this key. Raises ``httpx.HTTPError``/``ValueError``."""
    response = httpx.get(
        f"{url.rstrip('/')}{FEED_PATH}",
        params={"limit": 1},
        headers={"Authorization": f"Bearer {token}"},
        timeout=PROBE_TIMEOUT,
    )
    if response.status_code == 401:
        raise ValueError("hyperfix refused that API key")
    if response.status_code != 200:
        raise ValueError(f"hyperfix answered {response.status_code} for {FEED_PATH}")


@router.get("/auth/status", response_model=HyperfixStatusResponse)
async def hyperfix_auth_status(
    account: Optional[str] = None, db: AsyncSession = Depends(get_async_session)
) -> HyperfixStatusResponse:
    try:
        credentials = await hyperfix_credentials(db, account_ref("hyperfix", account))
    except ValueError:
        return HyperfixStatusResponse(status="needs-auth")
    return HyperfixStatusResponse(status="connected", url=credentials.base_url)


@router.post("/auth", response_model=HyperfixStatusResponse)
async def hyperfix_auth(
    body: HyperfixAuthRequest, db: AsyncSession = Depends(get_async_session)
) -> HyperfixStatusResponse:
    """Store a verified instance URL and key, for one account."""
    url = str(body.url).rstrip("/")
    try:
        _probe(url, body.token)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("hyperfix auth failed: %s", exc)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"could not connect to hyperfix: {exc}"
        ) from exc
    ref = account_ref("hyperfix", body.account)
    await upsert_credential(
        db, ref, token=body.token, base_url=url, label=body.label or "Hyperfix"
    )
    return HyperfixStatusResponse(status="connected", url=url)


@router.delete("/auth", response_model=HyperfixStatusResponse)
async def hyperfix_disconnect(
    account: Optional[str] = None, db: AsyncSession = Depends(get_async_session)
) -> HyperfixStatusResponse:
    await delete_credential(db, account_ref("hyperfix", account))
    return HyperfixStatusResponse(status="needs-auth")
