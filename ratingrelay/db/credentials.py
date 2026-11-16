"""
Shared DB helpers for ``ServiceAccount`` — read, upsert and soft-delete by ref.

An account is addressed by its ref (``lastfm``, ``lastfm:old``) rather than by a
kind, because a kind no longer names one account. See ``engine.refs`` for why the
first account keeps the bare kind forever.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from ratingrelay.engine.refs import ServiceRef, default_label, parse_ref
from ratingrelay.models.services import ServiceAccount, ServiceName


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def get_credential(db: AsyncSession, ref: str) -> ServiceAccount | None:
    """Return the live account stored under *ref*, or None.

    A removed (soft-deleted) account is not a credential: its token was wiped, and
    its ref must not be silently revived by a re-connect.
    """
    account = await db.get(ServiceAccount, _ref_of(ref))
    if account is None or account.deleted_at is not None:
        return None
    return account


async def get_account(db: AsyncSession, ref: str) -> ServiceAccount | None:
    """The row for *ref* whatever its state, so history can still name it."""
    return await db.get(ServiceAccount, _ref_of(ref))


async def list_accounts(
    db: AsyncSession, service: Optional[str] = None
) -> list[ServiceAccount]:
    """Every live account, oldest first, optionally only one kind's."""
    query = select(ServiceAccount).where(col(ServiceAccount.deleted_at).is_(None))
    if service is not None:
        query = query.where(ServiceAccount.service == ServiceName(parse_ref(service).kind))
    result = await db.execute(query.order_by(ServiceAccount.created_at))
    return list(result.scalars().all())


async def upsert_credential(
    db: AsyncSession, ref: str, **fields
) -> ServiceAccount:
    """Insert or update the account stored under *ref*.

    Keyword arguments are applied as field updates on the existing row (if any)
    or used to construct a new one.  ``updated_at`` is always refreshed.

    The bare kind is a **slot**, not an identity: it is the first account's ref
    for good, so disconnecting and reconnecting refills the same slot and the
    relay keeps reconciling against the history written under it (that is what
    "switch Plex server" and "replace my Last.fm account" already meant).

    A *named* ref is an identity. Re-connecting one after it was removed is
    refused: the writes recorded under it belong to an account whose credentials
    are gone, and a different account must not inherit them.
    """
    parsed = parse_ref(ref)
    existing = await get_account(db, parsed.ref)
    if existing is not None and existing.deleted_at is not None:
        if not parsed.is_primary:
            raise ValueError(
                f"{parsed.ref} was removed; add it under a different name "
                "rather than reusing a ref that already has history"
            )
        existing.deleted_at = None
    if existing:
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.updated_at = _now()
        account = existing
    else:
        account = ServiceAccount(
            id=parsed.ref,
            service=parsed.kind,
            label=fields.pop("label", "") or parsed.account or default_label(parsed.kind),
            **fields,
        )
        db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


async def delete_credential(db: AsyncSession, ref: str) -> bool:
    """Forget the credential for *ref*. True if there was one to forget.

    The row stays as a tombstone holding nothing secret, so the ref cannot be
    reissued to another account — while sync records and mappings, which are what
    the relay has already written, stay exactly as they are: dropping them would
    leave it unable to undo a write it made. The next run against a reconnected
    service reconciles against them.
    """
    account = await get_credential(db, ref)
    if account is None:
        return False
    account.deleted_at = _now()
    account.updated_at = account.deleted_at
    # A disconnected account keeps no secret material, and nothing that points at
    # the old server: the tombstone exists to hold the ref, nothing else. The
    # next connect refills the slot from scratch rather than inheriting a library.
    account.token = ""
    account.secret = None
    account.password_hash = None
    account.username = None
    account.plex_server_url = None
    account.plex_server_name = None
    await db.commit()
    return True


def _ref_of(ref: str | ServiceRef) -> str:
    """Accept a ref string or a :class:`ServiceRef`."""
    return ref.ref if isinstance(ref, ServiceRef) else parse_ref(ref).ref
