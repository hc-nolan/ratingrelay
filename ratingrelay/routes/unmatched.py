"""
Unmatched tracks and manual mapping API routes.

GET    /api/relay/unmatched          — list tracks that failed with "no match" errors
GET    /api/relay/mappings           — list all mappings
POST   /api/relay/mappings           — create a mapping
DELETE /api/relay/mappings/{id}      — delete a mapping
"""

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.db import get_async_session
from ratingrelay.models.jobs import TrackEvent
from ratingrelay.models.mappings import TrackMapping

router = APIRouter(prefix="/api/relay", tags=["mappings"])

_NO_MATCH_ERRORS = {
    "No match found in Plex library",
    "No MusicBrainz recording ID found",
}


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()


def _sync_key(artist: str, title: str) -> str:
    return f"{_normalize(artist)}||{_normalize(title)}"


class MappingCreate(BaseModel):
    source_artist: str
    source_title: str
    mapped_artist: str
    mapped_title: str
    recording_mbid: str | None = None
    target_service: str = "all"


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
    Return a deduplicated list of tracks that failed with no-match errors,
    along with whether a mapping exists for each.
    """
    result = await db.execute(
        select(TrackEvent)
        .where(TrackEvent.success == False)  # noqa: E712
        .where(TrackEvent.error.in_(list(_NO_MATCH_ERRORS)))
        .order_by(TrackEvent.created_at.desc())
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

    # Deduplicate by (artist, title, target_service), track occurrence count
    seen: dict[tuple[str, str, str], dict] = {}
    for ev in events:
        key = (ev.artist, ev.title, ev.target_service)
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
                "error": ev.error,
                "count": 0,
                "has_mapping": has_mapping,
            }
        seen[key]["count"] += 1

    return sorted(seen.values(), key=lambda x: x["count"], reverse=True)


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
        recording_mbid=body.recording_mbid or None,
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
