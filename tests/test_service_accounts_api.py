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

from ratingrelay.core import credentials as credential_resolution
from ratingrelay.core.session import SESSION_COOKIE
from ratingrelay.db import get_async_session
from ratingrelay.db.credentials import get_account, get_credential, upsert_credential
from ratingrelay.models.services import ServiceName
from ratingrelay.routes import lastfm, listenbrainz, plex, services
from ratingrelay.settings import Settings

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# Every credential setting pinned to its empty value, as the integration suite
# does: ``_env_file=None`` stops `.config` (which holds real tokens on a
# developer's machine) and the explicit values stop an exported PLEX_TOKEN or
# LISTENBRAINZ_TOKEN deciding what these tests assert. `/api/services` reads
# settings on every call, so without this a developer's shell can change the
# answer.
_UNSET = {
    "plex_token": None,
    "plex_server_url": None,
    "plex_music_library": "Music",
    "lastfm_token": None,
    "lastfm_secret": None,
    "lastfm_username": None,
    "lastfm_password": None,
    "listenbrainz_token": None,
    "listenbrainz_username": None,
    "hyperfix_url": None,
    "hyperfix_api_key": None,
}


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


class _FakePlexServer:
    """The server itself, asked only for its own name."""

    friendlyName = "Shelf"

    def __init__(self, url, token, timeout=None):
        self.url = url
        self.token = token
        self.timeout = timeout


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch):
    """Nothing in `.config` or the shell decides what these tests see."""
    monkeypatch.setattr(
        credential_resolution, "get_settings", lambda: Settings(_env_file=None, **_UNSET)
    )
    # The server-name memo is process-wide, on purpose; a test must not read a
    # name another test cached.
    monkeypatch.setattr(plex, "_server_names", {})


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


async def test_a_service_configured_from_the_environment_is_listed(
    client, factory, monkeypatch
):
    """A Key Vault deploy hands every credential in through env vars, not rows.

    There is no ``serviceaccount`` row for such a service, so listing rows alone
    left it out of the flow builder while every run resolved it happily — a
    connected target the interface refused to offer.
    """
    from ratingrelay.core import credentials as credentials_core  # pylint: disable=import-outside-toplevel
    from ratingrelay.settings import Settings  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(
        credentials_core,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            listenbrainz_token="from-settings",
            listenbrainz_username="chunned",
            hyperfix_url="https://hyperfix.invalid",
            hyperfix_api_key="from-settings",
        ),
    )

    body = (await client.get("/api/services")).json()

    assert [
        (a["id"], a["service"], a["primary"], a["configured"], a["managed"])
        for a in body["accounts"]
    ] == [
        ("listenbrainz", "listenbrainz", True, True, "settings"),
        ("hyperfix", "hyperfix", True, True, "settings"),
    ]
    by_id = {a["id"]: a for a in body["accounts"]}
    assert by_id["listenbrainz"]["username"] == "chunned"
    assert by_id["hyperfix"]["url"] == "https://hyperfix.invalid"


async def test_a_stored_account_is_not_listed_twice_because_settings_also_hold_it(
    client, factory, monkeypatch
):
    """Settings win field by field; they do not create a second account.

    A bare-kind row the environment fully describes is listed once, and as the
    environment's: replacing its credential here would not take effect.
    """
    from ratingrelay.core import credentials as credentials_core  # pylint: disable=import-outside-toplevel
    from ratingrelay.settings import Settings  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(
        credentials_core,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            listenbrainz_token="from-settings",
            listenbrainz_username="chunned",
        ),
    )
    await _store(factory, "listenbrainz", token="row", username="chunned", label="Mine")

    body = (await client.get("/api/services")).json()

    assert [
        (a["id"], a["label"], a["managed"], a["configured"]) for a in body["accounts"]
    ] == [("listenbrainz", "Mine", "settings", True)]


async def test_a_stored_account_the_environment_only_partly_describes_keeps_its_row(
    client, factory, monkeypatch
):
    """A Plex token without a server URL is not a credential the env owns."""
    from ratingrelay.core import credentials as credentials_core  # pylint: disable=import-outside-toplevel
    from ratingrelay.settings import Settings  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(
        credentials_core,
        "get_settings",
        lambda: Settings(_env_file=None, plex_token="from-settings"),
    )
    await _store(
        factory,
        "plex",
        token="row",
        username="chunned",
        plex_server_url="http://shelf.invalid:32400",
        plex_server_name="Shelf",
    )

    body = (await client.get("/api/services")).json()

    assert [(a["id"], a["managed"]) for a in body["accounts"]] == [("plex", "database")]


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


# ---------------------------------------------------------------------------
# Plex: a server the environment names is a server chosen
# ---------------------------------------------------------------------------


async def test_a_server_named_only_in_the_environment_is_reported(
    client, factory, monkeypatch
):
    """PLEX_SERVER_URL says which server; asking the user to pick it again is a bug.

    A deploy configured entirely from env has no row to hold a server name, so the
    status asks the server itself rather than leaving the wizard on "pick a server"
    forever. The name never changes, so the second page load must not ask again.
    """
    asked = []

    def _server(url, token, timeout=None):
        asked.append((url, token, timeout))
        return _FakePlexServer(url, token, timeout)

    monkeypatch.setattr(plex, "MyPlexAccount", _FakePlexAccount)
    monkeypatch.setattr(plex, "PlexServer", _server)
    monkeypatch.setattr(plex.settings, "plex_token", "from-settings")
    monkeypatch.setattr(plex.settings, "plex_server_url", "http://shelf.invalid:32400")

    body = (await client.get("/api/plex/auth/status")).json()
    again = (await client.get("/api/plex/auth/status")).json()

    assert body == {
        "status": "connected",
        "username": "chunned",
        "server_name": "Shelf",
        "server_url": "http://shelf.invalid:32400",
    }
    assert again["server_name"] == "Shelf"
    # The timeout is what keeps a page load bounded: losing it is a 30 s stall.
    assert asked == [("http://shelf.invalid:32400", "from-settings", plex._NAME_TIMEOUT)]


async def test_a_server_the_environment_moved_past_is_named_by_the_server(
    client, factory, monkeypatch
):
    """The name has to describe the server the relay reads, not the one it left."""
    monkeypatch.setattr(plex, "MyPlexAccount", _FakePlexAccount)
    monkeypatch.setattr(plex, "PlexServer", _FakePlexServer)
    monkeypatch.setattr(plex.settings, "plex_token", "from-settings")
    monkeypatch.setattr(plex.settings, "plex_server_url", "http://shelf.invalid:32400")
    await _store(
        factory,
        "plex",
        token="owner-token",
        username="chunned",
        plex_server_url="http://attic.invalid:32400",
        plex_server_name="Attic",
    )

    body = (await client.get("/api/plex/auth/status")).json()

    assert (body["server_name"], body["server_url"]) == (
        "Shelf",
        "http://shelf.invalid:32400",
    )


async def test_a_server_already_stored_is_not_asked_for_its_name_again(
    client, factory, monkeypatch
):
    def _server(*_args, **_kwargs):
        raise AssertionError("a stored server name is enough")

    monkeypatch.setattr(plex, "MyPlexAccount", _FakePlexAccount)
    monkeypatch.setattr(plex, "PlexServer", _server)
    monkeypatch.setattr(plex.settings, "plex_token", None)
    monkeypatch.setattr(plex.settings, "plex_server_url", None)
    await _store(
        factory,
        "plex",
        token="owner-token",
        username="chunned",
        plex_server_url="http://shelf.invalid:32400",
        plex_server_name="Shelf",
    )

    body = (await client.get("/api/plex/auth/status")).json()

    assert (body["status"], body["server_name"], body["server_url"]) == (
        "connected",
        "Shelf",
        "http://shelf.invalid:32400",
    )


async def test_an_unreachable_server_still_reports_the_login(
    client, factory, monkeypatch
):
    """A server that is down is not a bad token, and not a reason to ask again."""

    def _server(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(plex, "MyPlexAccount", _FakePlexAccount)
    monkeypatch.setattr(plex, "PlexServer", _server)
    monkeypatch.setattr(plex.settings, "plex_token", "from-settings")
    monkeypatch.setattr(plex.settings, "plex_server_url", "http://shelf.invalid:32400")

    body = (await client.get("/api/plex/auth/status")).json()

    assert (body["status"], body["server_name"], body["server_url"]) == (
        "connected",
        None,
        "http://shelf.invalid:32400",
    )


async def test_settings_never_describe_a_second_plex_server(
    client, factory, monkeypatch
):
    """The env describes the first account only; a named server answers for itself."""
    monkeypatch.setattr(plex, "MyPlexAccount", _FakePlexAccount)
    monkeypatch.setattr(
        plex,
        "PlexServer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("settings must not reach a named ref")
        ),
    )
    monkeypatch.setattr(plex.settings, "plex_token", "from-settings")
    monkeypatch.setattr(plex.settings, "plex_server_url", "http://shelf.invalid:32400")
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

    body = (
        await client.get("/api/plex/auth/status", params={"account": "attic"})
    ).json()

    assert (body["server_name"], body["server_url"]) == (
        "Attic",
        "http://attic.invalid:32400",
    )
