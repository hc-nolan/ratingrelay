from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core.relay import execute_relay
from ratingrelay.db import get_async_session
from ratingrelay.schemas.relay import (
    LastFMFromConfig,
    LastFMToConfig,
    ListenBrainzFromConfig,
    ListenBrainzToConfig,
    PlexFromConfig,
    PlexToConfig,
    RelayRequest,
    RelayResponse,
    TargetResult,
)
from ratingrelay.settings import logger

router = APIRouter(prefix="/api/relay", tags=["relay"])


# ---------------------------------------------------------------------------
# Relay endpoint — delegates to core/relay.py
# ---------------------------------------------------------------------------


@router.post("/run", response_model=RelayResponse)
async def run_relay(
    body: RelayRequest,
    db: AsyncSession = Depends(get_async_session),
) -> RelayResponse:
    """Fetch tracks from one service and relay them to one or more targets."""
    source = body.source
    if not isinstance(
        source, (PlexFromConfig, LastFMFromConfig, ListenBrainzFromConfig)
    ):
        raise HTTPException(status_code=400, detail="Unknown source service")

    try:
        result = await execute_relay(db, source, list(body.targets))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    tracks_fetched = result["tracks_fetched"]
    results_raw = result["results"]

    if not results_raw:
        return RelayResponse(status="error", tracks_fetched=tracks_fetched, results=[])

    target_results = [
        TargetResult(service=r["service"], processed=r["ok"], errors=r["err"])
        for r in results_raw
    ]

    total_errors = sum(r.errors for r in target_results)
    all_failed = all(r.processed == 0 for r in target_results)
    status = "error" if all_failed else ("partial" if total_errors else "completed")

    return RelayResponse(status=status, tracks_fetched=tracks_fetched, results=target_results)
