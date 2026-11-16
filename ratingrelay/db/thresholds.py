"""The stored love/hate thresholds: one row, read and upserted here."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.models.thresholds import RatingThresholds


async def get_thresholds(db: AsyncSession) -> Optional[RatingThresholds]:
    """The stored row, or None before the UI has saved one."""
    return await db.get(RatingThresholds, 1)


async def save_thresholds(
    db: AsyncSession, *, love: Optional[float], hate: Optional[float]
) -> RatingThresholds:
    """Store *love* and *hate*; ``None`` leaves that column as it was."""
    row = await get_thresholds(db) or RatingThresholds()
    if love is not None:
        row.love = love
    if hate is not None:
        row.hate = hate
    row.updated_at = datetime.now(tz=timezone.utc)
    db.add(row)
    await db.commit()
    return row
