from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    status: str = Field(default="queued")  # queued|running|scheduled|completed|partial|failed|cancelled
    source_config: str = Field()   # JSON string
    targets_config: str = Field()  # JSON string
    recurring: bool = Field(default=False)
    interval_minutes: Optional[int] = Field(default=None)
    run_at: Optional[datetime] = Field(default=None)  # for scheduled jobs
    created_at: datetime = Field(default_factory=_utcnow)
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    tracks_fetched: int = Field(default=0)
    tracks_ok: int = Field(default=0)
    tracks_err: int = Field(default=0)
    tracks_skipped: int = Field(default=0)


class TrackEvent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    job_id: str = Field(foreign_key="job.id", index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    artist: str = Field(default="")
    title: str = Field(default="")
    target_service: str = Field(default="")
    success: bool = Field(default=True)
    error: Optional[str] = Field(default=None)
