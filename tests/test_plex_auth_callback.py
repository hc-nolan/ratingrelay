"""The Plex OAuth callback must not race the confirmation Plex sends a moment later.

The popup is redirected back to us before Plex has necessarily confirmed the
PIN, so a single check can miss it. When that happened the credential was never
stored, the wizard stayed on "authenticate", and only a manual refresh (by which
time nothing had changed) made it look like a hang.

All fakes: no plex.tv, no server, no token.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import ratingrelay.models.services  # noqa: F401 — register tables
from ratingrelay.db import get_async_session
from ratingrelay.models.services import ServiceName
from ratingrelay.routes.plex import router as plex_router

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


class FakePinLogin:
    """A PIN that Plex confirms only after *confirm_after* checks."""

    def __init__(self, confirm_after: int = 0, token: str = "plex-token"):
        self._remaining = confirm_after
        self.token = token

    def checkLogin(self) -> bool:  # noqa: N802 - mirrors plexapi's name
        if self._remaining > 0:
            self._remaining -= 1
            return False
        return True


class FakeAccount:
    username = "chunned"


class Recorder:
    """Stands in for upsert_credential and remembers what was stored."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, _db, service, **kwargs):
        self.calls.append((service, kwargs))


@pytest.fixture
async def app_client():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    app = FastAPI()
    app.include_router(plex_router)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    await engine.dispose()


async def _callback(client: AsyncClient, pin_login, *, recorder: Recorder):
    """Hit the callback with fakes in place and no real delays."""
    with (
        patch("ratingrelay.routes.plex.iter_sessions", lambda: [(1, pin_login)]),
        patch("ratingrelay.routes.plex.MyPlexAccount", lambda token: FakeAccount()),
        patch("ratingrelay.routes.plex.upsert_credential", recorder),
        patch("ratingrelay.routes.plex.asyncio.sleep", AsyncMock()),
    ):
        return await client.get("/api/plex/auth/callback")


async def test_a_confirmed_pin_is_stored_and_the_popup_is_told(app_client):
    recorder = Recorder()

    response = await _callback(app_client, FakePinLogin(), recorder=recorder)

    assert recorder.calls == [(ServiceName.plex, {"token": "plex-token", "username": "chunned"})]
    assert "plex:authed" in response.text
    assert "window.close()" in response.text


async def test_a_pin_plex_confirms_a_moment_later_is_still_picked_up(app_client):
    """The regression: Plex answered on the third check, the first check won."""
    recorder = Recorder()

    response = await _callback(app_client, FakePinLogin(confirm_after=2), recorder=recorder)

    assert recorder.calls == [(ServiceName.plex, {"token": "plex-token", "username": "chunned"})]
    assert "plex:authed" in response.text


async def test_an_unconfirmed_pin_stores_nothing_and_says_so(app_client):
    recorder = Recorder()

    response = await _callback(
        app_client, FakePinLogin(confirm_after=99), recorder=recorder
    )

    assert recorder.calls == []
    assert "plex:authed" not in response.text
    assert "did not confirm" in response.text
