"""
Unmatched tracks, unrevertable writes and manual mapping API routes.

GET    /api/relay/unmatched          — list tracks the engine could not match
GET    /api/relay/unrevertable       — list recorded writes a reset must refuse
GET    /api/relay/mappings           — list all mappings
POST   /api/relay/mappings           — create a mapping
DELETE /api/relay/mappings/{id}      — delete a mapping
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.db import get_async_session
from ratingrelay.db.store import SqlSyncStore
from ratingrelay.engine.normalize import track_key
from ratingrelay.engine.services.registry import UNDO_PRECONDITIONS
from ratingrelay.engine.types import Reason
from ratingrelay.models.jobs import TrackEvent
from ratingrelay.models.mappings import TrackMapping
from ratingrelay.schemas.relay import UnrevertableRecord

router = APIRouter(prefix="/api/relay", tags=["mappings"])

# Selected on the engine's reason, never on the error string: the string is
# display text and may be reworded, the reason is the contract.
_UNMATCHED_REASONS = (
    Reason.NO_MATCH.value,
    Reason.AMBIGUOUS_MATCH.value,
    Reason.NO_RECORDING_MBID.value,
    Reason.MISSING_METADATA.value,
)


def _sync_key(artist: str, title: str) -> str:
    return track_key(artist, title)


class MappingCreate(BaseModel):
    source_artist: str
    source_title: str
    mapped_artist: str
    mapped_title: str
    recording_mbid: str | None = None
    target_service: str = "all"

    @field_validator("recording_mbid", mode="before")
    @classmethod
    def empty_string_to_none(cls, v: object) -> object:
        return None if v == "" else v


def _mapping_to_dict(m: TrackMapping) -> dict:
    return {
        "id": m.id,
        "source_artist": m.source_artist,
        "source_title": m.source_title,
        "mapped_artist": m.mapped_artist,
        "mapped_title": m.mapped_title,
        "recording_mbid": m.recording_mbid,
        "target_service": m.target_service,
        "created_at": m.created_at.isoformat(),
    }


@router.get("/unmatched")
async def list_unmatched(
    db: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    """
    Return the tracks that are unmatched *now*, along with whether a mapping
    exists for each.

    Every event is read, not just the failed ones: a track that failed in May
    and has synced on every run since is not a problem to act on, and showing it
    would bury the rows that are. So the newest event per track decides whether
    it appears at all, and the count is how many times it has failed since.
    """
    result = await db.execute(
        select(TrackEvent).order_by(
            TrackEvent.created_at.desc(), TrackEvent.id.desc()
        )
    )
    events = result.scalars().all()

    # Load all mappings once
    mapping_result = await db.execute(select(TrackMapping))
    all_mappings = mapping_result.scalars().all()
    mapped_keys: set[tuple[str, str]] = set()
    for m in all_mappings:
        mapped_keys.add((m.source_normalized_key, m.target_service))
        if m.target_service != "all":
            mapped_keys.add((m.source_normalized_key, "all"))

    # Deduplicate by (artist, title, target_service): the newest event decides
    # whether the track is listed, and every failure since then is counted.
    unmatched_reasons = set(_UNMATCHED_REASONS)
    settled: set[tuple[str, str, str]] = set()
    seen: dict[tuple[str, str, str], dict] = {}
    for ev in events:
        key = (ev.artist, ev.title, ev.target_service)
        if key in settled or ev.reason not in unmatched_reasons:
            # Newest event first: anything else for this key is history.
            settled.add(key)
            continue
        if key not in seen:
            norm_key = _sync_key(ev.artist, ev.title)
            has_mapping = (
                (norm_key, ev.target_service) in mapped_keys
                or (norm_key, "all") in mapped_keys
            )
            seen[key] = {
                "artist": ev.artist,
                "title": ev.title,
                "target_service": ev.target_service,
                "reason": ev.reason,
                "error": ev.error,
                "candidates": json.loads(ev.candidates) if ev.candidates else [],
                "count": 0,
                "has_mapping": has_mapping,
                "last_failed": ev.created_at.isoformat(),
            }
        seen[key]["count"] += 1

    return sorted(seen.values(), key=lambda x: x["count"], reverse=True)


@router.get("/unrevertable", response_model=list[UnrevertableRecord])
async def list_unrevertable(
    db: AsyncSession = Depends(get_async_session),
) -> list[UnrevertableRecord]:
    """
    Return the writes a reset would refuse, because the record does not say
    where it was written.

    Each target answers for its own rows through the same precondition its own
    undo runs, so this list and a real reset cannot disagree about which rows
    are undoable. It reads records, not job events: the answer describes the
    records as they are now and does not need a reset to have run.
    """
    refusals: list[UnrevertableRecord] = []
    for target_service, record in await SqlSyncStore(db).all_records():
        precondition = UNDO_PRECONDITIONS.get(target_service)
        if precondition is None:
            continue  # a target we no longer write to has nothing to refuse
        try:
            precondition(record)
        except ValueError as exc:
            refusals.append(
                UnrevertableRecord(
                    artist=record.track.artist,
                    title=record.track.title,
                    target_service=target_service,
                    intent=record.intent,
                    error=str(exc),
                )
            )
    return refusals


@router.get("/mappings")
async def list_mappings(
    db: AsyncSession = Depends(get_async_session),
) -> list[dict]:
    result = await db.execute(select(TrackMapping).order_by(TrackMapping.created_at.desc()))
    return [_mapping_to_dict(m) for m in result.scalars().all()]


@router.post("/mappings", status_code=201)
async def create_mapping(
    body: MappingCreate,
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    norm_key = _sync_key(body.source_artist, body.source_title)

    # Check for duplicate
    existing = await db.execute(
        select(TrackMapping).where(
            TrackMapping.source_normalized_key == norm_key,
            TrackMapping.target_service == body.target_service,
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=409,
            detail="A mapping for this track and service already exists",
        )

    mapping = TrackMapping(
        source_normalized_key=norm_key,
        source_artist=body.source_artist,
        source_title=body.source_title,
        mapped_artist=body.mapped_artist,
        mapped_title=body.mapped_title,
        recording_mbid=body.recording_mbid,
        target_service=body.target_service,
    )
    db.add(mapping)
    await db.commit()
    await db.refresh(mapping)
    return _mapping_to_dict(mapping)


@router.delete("/mappings/{mapping_id}", status_code=200)
async def delete_mapping(
    mapping_id: str,
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    mapping = await db.get(TrackMapping, mapping_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Mapping not found")
    await db.delete(mapping)
    await db.commit()
    return {"deleted": mapping_id}
