"""Signing in as the owner, and what the guard lets through.

The guard is the only thing between the internet and a button that resets a
service, so these tests are deliberately about *refusals*: who does not get in,
and what an unsigned request can still reach.
"""

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from ratingrelay.core import session as session_module
from ratingrelay.core.session import (
    SESSION_COOKIE,
    issue_session,
    verify_session,
)
from ratingrelay.db import get_async_session
from ratingrelay.models.services import ServiceCredential, ServiceName
from ratingrelay.routes.auth import router as auth_router

OWNER_TOKEN = "owner-plex-token"
APP_SECRET = "test-secret"


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    class _S:
        secret = APP_SECRET
        auth = True
        base_url = "http://localhost:8100"
        plex_token = None
        plex_server_url = None
        plex_music_library = "Music"

    monkeypatch.setattr(session_module, "get_settings", lambda: _S)
    return _S


@pytest.fixture
async def engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def client(factory, monkeypatch):
    from ratingrelay.core.session import session_guard

    app = FastAPI()
    app.include_router(auth_router)
    app.middleware("http")(session_guard(session_factory=factory))

    @app.get("/api/relay/run")
    async def _protected():
        return {"ok": True}

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={}
    ) as ac:
        yield ac


async def _configure_owner(factory) -> None:
    async with factory() as db:
        db.add(
            ServiceCredential(
                service=ServiceName.plex,
                token=OWNER_TOKEN,
                username="chunned",
            )
        )
        await db.commit()


# ---------------------------------------------------------------------------
# The cookie
# ---------------------------------------------------------------------------


def test_a_session_round_trips():
    cookie = issue_session("12345", OWNER_TOKEN)
    assert verify_session(cookie, OWNER_TOKEN) == "12345"


def test_a_session_signed_with_another_token_is_rejected():
    """Rotating the Plex token has to invalidate every browser."""
    cookie = issue_session("12345", OWNER_TOKEN)
    assert verify_session(cookie, "a-different-token") is None


def test_a_tampered_session_is_rejected():
    cookie = issue_session("12345", OWNER_TOKEN)
    assert verify_session(cookie.replace("12345", "99999"), OWNER_TOKEN) is None


def test_a_change_of_secret_signs_everyone_out(monkeypatch):
    cookie = issue_session("12345", OWNER_TOKEN)

    class _Other:
        secret = "rotated"
        auth = True

    monkeypatch.setattr(session_module, "get_settings", lambda: _Other)
    assert verify_session(cookie, OWNER_TOKEN) is None


def test_garbage_is_not_a_session():
    for value in ["", "nope", "v1.x.1.y", "v2.1.1.mac", "v1.1.1"]:
        assert verify_session(value, OWNER_TOKEN) is None


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


async def test_a_fresh_install_needs_no_login(client):
    """Setup has to be reachable, or there is no way to configure anything."""
    res = await client.get("/api/auth/status")

    assert res.status_code == 200
    assert res.json() == {
        "required": False,
        "authenticated": False,
        "username": None,
        "cookie": False,
    }


async def test_a_configured_relay_asks_for_a_login(client, factory, plex_identifies):
    await _configure_owner(factory)


    res = await client.get("/api/auth/status")

    body = res.json()
    assert body["required"] is True
    assert body["authenticated"] is False
    assert body["username"] == "chunned"
    # No cookie yet: the gate must be able to tell this from a rejected one.
    assert body["cookie"] is False


async def test_a_signed_in_browser_reports_itself(client, factory, plex_identifies):
    await _configure_owner(factory)

    client.cookies.set(SESSION_COOKIE, issue_session("12345", OWNER_TOKEN))

    res = await client.get("/api/auth/status")

    assert res.json()["authenticated"] is True
    assert res.json()["cookie"] is True


async def test_login_can_be_switched_off_for_a_private_relay(client, factory, settings):
    await _configure_owner(factory)
    settings.auth = False

    res = await client.get("/api/auth/status")

    assert res.json()["required"] is False


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@pytest.fixture
def plex_identifies(monkeypatch):
    """Make Plex answer "who is this token?" without a network call.

    Returns the token -> account map, so a test can add a second account and
    check that it is not mistaken for the owner.
    """
    from ratingrelay.routes import auth as auth_routes

    accounts: dict[str, tuple[str, str]] = {OWNER_TOKEN: ("12345", "chunned")}

    async def _identify(token: str):
        return accounts.get(token)

    monkeypatch.setattr(session_module, "identify", _identify)
    monkeypatch.setattr(auth_routes, "identify", _identify)
    return accounts


class _FakePin:
    def __init__(self, token):
        self.token = token
        self._id = 77

    def checkLogin(self):  # noqa: N802 - plexapi's spelling
        return self.token is not None


@pytest.fixture
def pins(monkeypatch):
    """Stub the Plex PIN store so no test ever talks to plex.tv."""
    from ratingrelay.routes import auth as auth_routes

    state = {"token": None, "removed": []}

    def _create(forward_url):
        assert forward_url.endswith("/api/auth/plex/done")
        pin = _FakePin(None)
        pin._id = 77
        return pin, "https://app.plex.tv/auth"

    monkeypatch.setattr(auth_routes, "create_session", _create)
    monkeypatch.setattr(auth_routes, "get_session", lambda pin_id: _FakePin(state["token"]))
    monkeypatch.setattr(
        auth_routes, "remove_session", lambda pin_id: state["removed"].append(pin_id)
    )
    return state


async def test_confirming_as_the_owner_sets_a_session(client, factory, pins, plex_identifies):
    await _configure_owner(factory)

    pins["token"] = OWNER_TOKEN

    res = await client.get("/api/auth/plex/poll", params={"pin_id": 77})

    assert res.status_code == 200
    assert res.json() == {"status": "ok", "username": "chunned"}
    cookie = res.cookies.get(SESSION_COOKIE)
    assert cookie and verify_session(cookie, OWNER_TOKEN) == "12345"
    assert "httponly" in res.headers["set-cookie"].lower()


async def test_a_login_mints_a_new_token_but_signs_with_the_configured_one(
    client, factory, pins, plex_identifies
):
    """Plex hands out a fresh token per login; the guard only knows the stored one.

    Signing the cookie with the token this PIN just minted produces a session that
    looks fine and verifies against nothing — the login appears to do nothing.
    """
    await _configure_owner(factory)
    plex_identifies["freshly-minted-token"] = ("12345", "chunned")
    pins["token"] = "freshly-minted-token"

    res = await client.get("/api/auth/plex/poll", params={"pin_id": 77})

    assert res.status_code == 200
    cookie = res.cookies.get(SESSION_COOKIE)
    assert cookie
    assert verify_session(cookie, OWNER_TOKEN) == "12345"

    # And the browser stays signed in: the very next request passes the guard.
    status = await client.get("/api/auth/status")
    assert status.json()["authenticated"] is True
    assert status.json()["cookie"] is True
    assert (await client.get("/api/relay/run")).status_code == 200


async def test_confirming_as_somebody_else_is_refused(client, factory, pins, plex_identifies):
    """Anyone can reach a relay on the LAN; only the owner gets a session."""
    await _configure_owner(factory)
    plex_identifies["stranger-token"] = ("99999", "stranger")
    pins["token"] = "stranger-token"

    res = await client.get("/api/auth/plex/poll", params={"pin_id": 77})

    assert res.status_code == 403
    assert SESSION_COOKIE not in res.cookies


async def test_an_unconfirmed_pin_is_just_pending(client, factory, pins):
    await _configure_owner(factory)
    pins["token"] = None

    res = await client.get("/api/auth/plex/poll", params={"pin_id": 77})

    assert res.status_code == 200
    assert res.json() == {"status": "pending", "username": None}


async def test_an_unknown_pin_is_gone(client, pins, monkeypatch):
    from ratingrelay.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes, "get_session", lambda pin_id: None)
    res = await client.get("/api/auth/plex/poll", params={"pin_id": 1})

    assert res.status_code == 404


async def test_logout_clears_the_cookie(client, factory):
    await _configure_owner(factory)
    client.cookies.set(SESSION_COOKIE, issue_session("12345", OWNER_TOKEN))

    res = await client.post("/api/auth/logout")

    assert res.status_code == 200
    assert SESSION_COOKIE not in res.cookies


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


async def test_a_protected_route_refuses_an_unsigned_request(client, factory):
    await _configure_owner(factory)

    res = await client.get("/api/relay/run")

    assert res.status_code == 401
    assert res.json()["detail"] == "Not signed in"


async def test_a_protected_route_accepts_a_signed_request(
    client, factory, plex_identifies
):
    await _configure_owner(factory)
    client.cookies.set(SESSION_COOKIE, issue_session("12345", OWNER_TOKEN))

    res = await client.get("/api/relay/run")

    assert res.status_code == 200


async def test_the_login_endpoints_stay_open_while_unsigned(client, factory):
    await _configure_owner(factory)

    assert (await client.get("/api/auth/status")).status_code == 200
    assert (await client.get("/api/auth/plex/done")).status_code == 200


async def test_a_fresh_install_is_not_locked_out_of_itself(client):
    """No owner yet means no one to authenticate as; setup has to work."""
    res = await client.get("/api/relay/run")

    assert res.status_code == 200


async def test_auth_false_opens_the_api(client, factory, settings):
    await _configure_owner(factory)
    settings.auth = False

    res = await client.get("/api/relay/run")

    assert res.status_code == 200


async def test_a_session_from_another_install_is_refused(client, factory):
    await _configure_owner(factory)
    client.cookies.set(SESSION_COOKIE, issue_session("12345", "someone-elses-token"))

    res = await client.get("/api/relay/run")

    assert res.status_code == 401
