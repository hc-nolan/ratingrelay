"""Adding, listing and removing a second account of a service, from the API.

A second account is an opt-in act on the services page: the first one keeps the
bare kind as its ref, so nothing the relay has written moves, and the second one
gets a name of its own. These tests follow one Last.fm account, one ListenBrainz
account and one extra Plex server all the way through the endpoints the page
calls, and check the two properties that matter: the second account is addressed
separately, and the first one is left alone.
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
import ratingrelay.models.thresholds  # noqa: F401

from ratingrelay.core.session import SESSION_COOKIE
from ratingrelay.db import get_async_session
from ratingrelay.db.credentials import get_account, get_credential, upsert_credential
from ratingrelay.models.services import ServiceName
from ratingrelay.routes import lastfm, listenbrainz, plex, services

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


class _FakePasswordUser:
    def get_name(self):
        return "someone"


class _FakeNetworkWithUser:
    def __init__(self, **_kwargs):
        pass

    def get_authenticated_user(self):
        return _FakePasswordUser()


class _FakeConnection:
    def __init__(self, baseurl: str):
        self._baseurl = baseurl


class _FakePlexAccount:
    """The owner's Plex account, listing the servers it can see."""

    username = "chunned"

    def __init__(self, **_kwargs):
        pass

    def resource(self, name: str):
        return _FakeResource(name)


class _FakeResource:
    def __init__(self, name: str):
        self.name = name

    def connect(self):
        return _FakeConnection(f"http://{self.name.lower()}.invalid:32400")


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
    for router in (lastfm.router, listenbrainz.router, plex.router, services.router):
        app.include_router(router)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _store(factory, ref: str, **fields):
    async with factory() as db:
        await upsert_credential(db, ref, **fields)


async def _get(factory, ref: str):
    async with factory() as db:
        return await get_credential(db, ref)


async def _row(factory, ref: str):
    async with factory() as db:
        return await get_account(db, ref)


# ---------------------------------------------------------------------------
# The list the services page reads
# ---------------------------------------------------------------------------


async def test_the_list_reports_every_account_and_which_is_first(client, factory):
    await _store(
        factory,
        "listenbrainz",
        token="tok",
        username="tester",
        label="ListenBrainz",
    )
    await _store(
        factory,
        "listenbrainz:new",
        token="tok2",
        username="tester-new",
        label="New account",
    )

    body = (await client.get("/api/services")).json()

    assert [
        (a["id"], a["service"], a["label"], a["primary"], a["configured"], a["username"])
        for a in body["accounts"]
    ] == [
        ("listenbrainz", "listenbrainz", "ListenBrainz", True, True, "tester"),
        ("listenbrainz:new", "listenbrainz", "New account", False, True, "tester-new"),
    ]


async def test_an_account_with_incomplete_credentials_is_not_configured(client, factory):
    """A Plex row without a server is a token we cannot read a library with."""
    await _store(factory, "plex", token="plex-token", username="chunned")

    body = (await client.get("/api/services")).json()

    assert [a["configured"] for a in body["accounts"]] == [False]


# ---------------------------------------------------------------------------
# ListenBrainz: a second account
# ---------------------------------------------------------------------------


async def test_a_second_listenbrainz_account_is_stored_under_its_own_ref(
    client, factory, monkeypatch
):
    monkeypatch.setattr(listenbrainz, "_validate_token", lambda username, token: username)
    await _store(factory, "listenbrainz", token="tok", username="tester")

    res = await client.post(
        "/api/listenbrainz/auth",
        json={"username": "tester-new", "token": "tok2", "account": "new", "label": "New account"},
    )

    assert res.status_code == 200
    second = await _get(factory, "listenbrainz:new")
    assert second is not None
    assert (second.label, second.username, second.token) == ("New account", "tester-new", "tok2")
    # The first account is not rewritten by connecting a second one.
    first = await _get(factory, "listenbrainz")
    assert (first.username, first.token) == ("tester", "tok")


async def test_a_second_account_is_asked_about_by_name(client, factory):
    await _store(factory, "listenbrainz", token="tok", username="tester")

    assert (
        await client.get("/api/listenbrainz/auth/status", params={"account": "new"})
    ).json()["status"] == "needs-auth"

    await _store(factory, "listenbrainz:new", token="tok2", username="tester-new")
    body = (
        await client.get("/api/listenbrainz/auth/status", params={"account": "new"})
    ).json()
    assert body == {"status": "connected", "username": "tester-new"}


async def test_settings_describe_the_first_account_only(client, factory, monkeypatch):
    """An environment variable cannot name a second account."""
    from ratingrelay.core import credentials as credentials_core  # pylint: disable=import-outside-toplevel
    from ratingrelay.settings import Settings  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(
        credentials_core,
        "get_settings",
        lambda: Settings(_env_file=None, listenbrainz_token="from-settings"),
    )
    await _store(factory, "listenbrainz", token="row", username="first")
    await _store(factory, "listenbrainz:new", token="row-new", username="second")

    first = (
        await client.get("/api/listenbrainz/auth/status")
    ).json()
    second = (
        await client.get("/api/listenbrainz/auth/status", params={"account": "new"})
    ).json()

    assert first["username"] == "first"
    assert second["username"] == "second"


async def test_removing_a_second_account_leaves_the_first_one(client, factory):
    await _store(factory, "listenbrainz", token="tok", username="tester")
    await _store(factory, "listenbrainz:new", token="tok2", username="tester-new")

    res = await client.delete("/api/listenbrainz/auth", params={"account": "new"})

    assert res.status_code == 200
    assert await _get(factory, "listenbrainz:new") is None
    assert await _row(factory, "listenbrainz:new") is not None  # the ref is still taken
    assert await _get(factory, "listenbrainz") is not None


# ---------------------------------------------------------------------------
# Last.fm: the password form, for either account
# ---------------------------------------------------------------------------


async def test_a_second_lastfm_account_gets_the_label_it_was_given(
    client, factory, monkeypatch
):
    monkeypatch.setattr(lastfm.pylast, "md5", lambda password: "hash")
    monkeypatch.setattr(lastfm.pylast, "LastFMNetwork", _FakeNetworkWithUser)

    res = await client.post(
        "/api/lastfm/auth",
        json={
            "api_key": "key",
            "api_secret": "secret",
            "username": "someone",
            "password": "hunter2",
            "account": "old",
            "label": "Old account",
        },
    )

    assert res.status_code == 200
    account = await _get(factory, "lastfm:old")
    assert (account.id, account.label, account.username) == (
        "lastfm:old",
        "Old account",
        "someone",
    )


# ---------------------------------------------------------------------------
# Plex: a second server is a second account
# ---------------------------------------------------------------------------


async def test_a_second_plex_server_keeps_its_own_url_and_the_owner_token(
    client, factory, monkeypatch
):
    monkeypatch.setattr(plex, "MyPlexAccount", _FakePlexAccount)
    await _store(
        factory,
        "plex",
        token="owner-token",
        username="chunned",
        plex_server_url="http://shelf.invalid:32400",
        plex_server_name="Shelf",
        label="Plex",
    )

    res = await client.post(
        "/api/plex/server",
        json={"name": "Attic", "account": "attic", "label": "Attic"},
    )

    assert res.status_code == 200
    attic = await _get(factory, "plex:attic")
    assert (attic.plex_server_name, attic.plex_server_url) == (
        "Attic",
        "http://attic.invalid:32400",
    )
    # One Plex login, two servers: the same token reads both.
    assert attic.token == "owner-token"
    assert attic.username == "chunned"
    # And the primary server is untouched by adding one.
    primary = await _get(factory, "plex")
    assert primary.plex_server_name == "Shelf"


async def test_removing_a_second_plex_server_keeps_the_login(client, factory):
    await _store(
        factory,
        "plex",
        token="owner-token",
        username="chunned",
        plex_server_url="http://shelf.invalid:32400",
        plex_server_name="Shelf",
    )
    await _store(
        factory,
        "plex:attic",
        token="owner-token",
        username="chunned",
        plex_server_url="http://attic.invalid:32400",
        plex_server_name="Attic",
    )

    res = await client.delete("/api/plex/auth", params={"account": "attic"})

    assert res.status_code == 200
    # The session was signed with the primary server's token, so it survives.
    assert SESSION_COOKIE not in (res.headers.get("set-cookie") or "")
    assert await _get(factory, "plex") is not None


async def test_removing_the_primary_plex_account_ends_the_login(client, factory):
    await _store(factory, "plex", token="owner-token", username="chunned")

    res = await client.delete("/api/plex/auth")

    assert SESSION_COOKIE in (res.headers.get("set-cookie") or "")


# ---------------------------------------------------------------------------
# A bad account name is refused before it can be stored
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("account", ["New", "new one", "-new", ""])
async def test_an_account_name_that_would_not_parse_is_refused(client, account):
    res = await client.post(
        "/api/listenbrainz/auth",
        json={"username": "tester", "token": "tok", "account": account},
    )

    assert res.status_code == 422
