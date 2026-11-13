"""Disconnecting a service forgets the credential, and only the credential.

A user who connects one service at first run has to be able to add or replace
the other later, which means a way to say "forget this". What that must not do
is throw away the record of what the relay has already written: those rows are
the only proof it owns a rating over there, and losing them turns every future
reset into "unrecorded target".
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import ratingrelay.models.jobs  # noqa: F401 — register tables
import ratingrelay.models.mappings  # noqa: F401
import ratingrelay.models.services  # noqa: F401
import ratingrelay.models.sync  # noqa: F401

from ratingrelay.core.session import SESSION_COOKIE
from ratingrelay.db import get_async_session
from ratingrelay.db.credentials import get_credential, upsert_credential
from ratingrelay.models.services import ServiceName
from ratingrelay.models.sync import SyncRecord
from ratingrelay.routes import lastfm, listenbrainz, plex

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def factory():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(factory):
    app = FastAPI()
    app.include_router(lastfm.router)
    app.include_router(listenbrainz.router)
    app.include_router(plex.router)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _store(factory, service: ServiceName, **fields):
    async with factory() as db:
        await upsert_credential(db, service, **fields)


async def _cred(factory, service: ServiceName):
    async with factory() as db:
        return await get_credential(db, service)


async def test_lastfm_disconnect_removes_the_credential(client, factory):
    await _store(
        factory,
        ServiceName.lastfm,
        token="key",
        secret="secret",
        username="someone",
        password_hash="hash",
    )

    res = await client.delete("/api/lastfm/auth")

    assert res.status_code == 200
    assert res.json()["status"] == "needs-auth"
    assert await _cred(factory, ServiceName.lastfm) is None


async def test_listenbrainz_disconnect_removes_the_credential(client, factory):
    await _store(factory, ServiceName.listenbrainz, token="tok", username="someone")

    res = await client.delete("/api/listenbrainz/auth")

    assert res.status_code == 200
    assert res.json()["status"] == "needs-auth"
    assert await _cred(factory, ServiceName.listenbrainz) is None


async def test_disconnecting_twice_is_not_an_error(client):
    await client.delete("/api/lastfm/auth")

    res = await client.delete("/api/lastfm/auth")

    assert res.status_code == 200
    assert res.json()["status"] == "needs-auth"


async def test_disconnecting_keeps_what_the_relay_has_written(client, factory):
    """The sync records are the proof of ownership; they survive a disconnect."""
    await _store(factory, ServiceName.lastfm, token="key", secret="s", username="u")
    async with factory() as db:
        db.add(
            SyncRecord(
                source_service="plex",
                target_service="lastfm",
                normalized_key="artist||title",
                intent="love",
            )
        )
        await db.commit()

    await client.delete("/api/lastfm/auth")

    async with factory() as db:
        rows = (await db.execute(SQLModel.metadata.tables["syncrecord"].select())).all()
    assert len(rows) == 1


async def test_plex_disconnect_clears_the_session_cookie(client, factory):
    """Without the Plex token there is nothing to verify the cookie against."""
    await _store(factory, ServiceName.plex, token="plex-token", username="owner")

    res = await client.delete("/api/plex/auth")

    assert res.status_code == 200
    assert res.json()["status"] == "needs-auth"
    assert SESSION_COOKIE in res.headers.get("set-cookie", "")
    assert await _cred(factory, ServiceName.plex) is None
