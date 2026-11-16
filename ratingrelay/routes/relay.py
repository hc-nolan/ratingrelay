from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.core import adapters
from ratingrelay.core.factory import unsupported_target
from ratingrelay.core.relay import execute_relay
from ratingrelay.core.thresholds import Thresholds, effective_thresholds
from ratingrelay.db import get_async_session
from ratingrelay.db.thresholds import save_thresholds
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
    ThresholdsResponse,
    ThresholdsUpdate,
)
from ratingrelay.settings import get_settings, logger

router = APIRouter(prefix="/api/relay", tags=["relay"])


# ---------------------------------------------------------------------------
# Relay endpoint — delegates to core/relay.py
# ---------------------------------------------------------------------------


def _thresholds_body(current: Thresholds) -> ThresholdsResponse:
    return ThresholdsResponse(
        love_threshold=current.love,
        hate_threshold=current.hate,
        love_locked=current.love_locked,
        hate_locked=current.hate_locked,
    )


@router.get("/thresholds", response_model=ThresholdsResponse)
async def thresholds(
    db: AsyncSession = Depends(get_async_session),
) -> ThresholdsResponse:
    """The Plex ratings that count as a love and a hate, shared by every relay."""
    return _thresholds_body(await effective_thresholds(db, get_settings()))


@router.put("/thresholds", response_model=ThresholdsResponse)
async def update_thresholds(
    body: ThresholdsUpdate,
    db: AsyncSession = Depends(get_async_session),
) -> ThresholdsResponse:
    """Change the love/hate thresholds for every relay, scheduled ones included.

    409 for a threshold set in settings; 422 if the pair would overlap.
    """
    current = await effective_thresholds(db, get_settings())
    love, hate = body.love_threshold, body.hate_threshold
    # Sending back the value a locked one already has is not a change.
    if love is not None and current.love_locked and love != current.love:
        raise HTTPException(409, "the love threshold is set by LOVE_THRESHOLD")
    if hate is not None and current.hate_locked and hate != current.hate:
        raise HTTPException(409, "the hate threshold is set by HATE_THRESHOLD")
    proposed = replace(
        current,
        love=current.love if love is None else float(love),
        hate=current.hate if hate is None else float(hate),
    )
    overlap = proposed.overlap()
    if overlap:
        raise HTTPException(422, overlap)
    await save_thresholds(
        db,
        love=None if current.love_locked else proposed.love,
        hate=None if current.hate_locked else proposed.hate,
    )
    logger.info("Thresholds set: love %g, hate %g", proposed.love, proposed.hate)
    return _thresholds_body(proposed)


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
    relay = adapters.relay_for(source)
    for target in body.targets:
        refusal = unsupported_target(relay, target.service)
        if refusal:
            raise HTTPException(status_code=422, detail=refusal)

    try:
        result = await execute_relay(
            db, source, list(body.targets), dry_run=body.dry_run
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    tracks_fetched = result["tracks_fetched"]
    results_raw = result["results"]

    if not results_raw:
        return RelayResponse(
            status="error", tracks_fetched=tracks_fetched, results=[], dry_run=body.dry_run
        )

    target_results = [
        TargetResult(
            service=r["service"],
            processed=r["ok"],
            errors=r["err"],
            skipped=r.get("skipped", 0),
        )
        for r in results_raw
    ]

    # A dry run writes nothing, so "processed" alone would call it a failure.
    # Judge it on whether the pipeline got through, which is what it reports.
    attempted = any(r.processed or r.skipped or r.errors for r in target_results)
    total_errors = sum(r.errors for r in target_results)
    status = "error" if not attempted else ("partial" if total_errors else "completed")

    return RelayResponse(
        status=status,
        tracks_fetched=tracks_fetched,
        results=target_results,
        dry_run=body.dry_run,
    )
