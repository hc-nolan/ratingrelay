"""
Shared DB helpers for ServiceCredential — get and upsert per-service credentials.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.models.services import ServiceCredential, ServiceName


async def get_credential(
    db: AsyncSession, service: ServiceName
) -> ServiceCredential | None:
    """Return the stored credential for *service*, or None if not present."""
    result = await db.execute(
        select(ServiceCredential).where(ServiceCredential.service == service)
    )
    return result.scalar_one_or_none()


async def upsert_credential(
    db: AsyncSession, service: ServiceName, **fields
) -> ServiceCredential:
    """Insert or update the credential row for *service*.

    Keyword arguments are applied as field updates on the existing row (if any)
    or used to construct a new one.  ``updated_at`` is always refreshed.
    """
    existing = await get_credential(db, service)
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.updated_at = datetime.now(tz=timezone.utc)
        cred = existing
    else:
        cred = ServiceCredential(service=service, **fields)
        db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return cred


async def delete_credential(db: AsyncSession, service: ServiceName) -> bool:
    """Forget the credential for *service*. True if there was one to forget.

    Only the credential row goes. Sync records stay: they are what the relay has
    already written, and dropping them would leave it unable to undo a write it
    made — the next run against a reconnected service reconciles against them.
    """
    existing = await get_credential(db, service)
    if existing is None:
        return False
    await db.delete(existing)
    await db.commit()
    return True
