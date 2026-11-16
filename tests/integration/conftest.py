"""Fixtures for the integration suite: a world, and the database it writes to.

A test never reads ``.config``: settings come from the model defaults and the
credentials are rows this fixture seeds, which is what a fresh install holds
after the setup form. Nothing here can reach a real Plex server, last.fm account
or ListenBrainz token, and `tests/conftest.py` builds the in-memory database.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ratingrelay.core import credentials as credential_resolution
from ratingrelay.core import factory
from ratingrelay.models.services import ServiceCredential, ServiceName
from ratingrelay.settings import Settings

from tests.integration import fakes


# Every credential setting pinned to its empty value. ``_env_file=None`` stops
# `.config` being read; passing them explicitly stops a developer's exported
# PLEX_TOKEN (or similar) deciding what these tests assert about the
# connected service, since settings beat the credential row.
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
}


@pytest.fixture
async def session(db_engine) -> AsyncSession:
    """One session for a whole test, so a second run sees the first run's rows."""
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest.fixture
def settings(monkeypatch) -> Settings:
    """The app's settings with no env file and nothing pinned from the shell.

    ``factory`` and ``core/credentials`` both read ``get_settings`` through their
    own module attributes, so both are patched: a tracked ``.config`` (which
    holds real tokens on a developer machine, or real thresholds) must not be
    able to change what these tests assert.
    """
    for name in ("LOVE_THRESHOLD", "HATE_THRESHOLD"):
        monkeypatch.delenv(name, raising=False)
    clean = Settings(_env_file=None, **_UNSET)
    monkeypatch.setattr(factory, "get_settings", lambda: clean)
    monkeypatch.setattr(credential_resolution, "get_settings", lambda: clean)
    return clean


@pytest.fixture
async def world(session, settings, monkeypatch) -> fakes.World:
    """Three configured services, a MusicBrainz lookup, and the fakes behind them."""
    del settings  # the values are not needed; patching it is
    world = fakes.World(
        session=session,
        plex=fakes.FakePlex(),
        lastfm=fakes.FakeLastFm(),
        listenbrainz=fakes.FakeListenBrainz(),
        brainz=fakes.FakeMusicBrainz(),
    )
    await _connect(session)
    fakes.install(monkeypatch, world)
    return world


async def _connect(session: AsyncSession) -> None:
    """The credential rows a user's setup leaves behind, and nothing more."""
    session.add_all(
        [
            ServiceCredential(
                service=ServiceName.plex,
                token="plex-token",
                plex_server_url="http://plex.invalid:32400",
                plex_server_name="Test Plex",
            ),
            ServiceCredential(
                service=ServiceName.lastfm,
                token="lastfm-api-key",
                secret="lastfm-api-secret",
                username="tester",
                password_hash="00000000000000000000000000000000",
            ),
            ServiceCredential(
                service=ServiceName.listenbrainz,
                token="listenbrainz-token",
                username="tester",
            ),
        ]
    )
    await session.commit()
