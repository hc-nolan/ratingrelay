from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TrackMapping(SQLModel, table=True):
    """User-defined override: if a track can't be found, use these values instead."""
    __tablename__ = "trackmapping"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    # Original (failing) track, normalized for lookup
    source_normalized_key: str = Field()  # _normalize(artist) + "||" + _normalize(title)
    source_artist: str = Field()
    source_title: str = Field()
    # Override values to use when searching in the target service
    mapped_artist: str = Field()
    mapped_title: str = Field()
    # For ListenBrainz: skip search and use this MBID directly
    recording_mbid: Optional[str] = Field(default=None)
    # "plex", "lastfm", "listenbrainz", or "all"
    target_service: str = Field(default="all")
    created_at: datetime = Field(default_factory=_utcnow)
