"""A relay that names a second account of a service.

The wire format gained one optional field: ``account``, the slug after the colon
in an account's ref. Everything that names no account means the account that
already existed, so the payloads below are the old ones wherever the point is
not the account.

What these pin: the field survives the queue, a same-kind pairing is accepted
while the same account relaying into itself is refused, a reset names one
account, and a reset of one account dispatches the wipe for that account — never
for its kind, and never for a different service.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ratingrelay.core import relay as relay_core
from ratingrelay.schemas.relay import (
    ListenBrainzToConfig,
    LastFMToConfig,
    PlexToConfig,
)

PLEX_SOURCE = {"service": "plex", "feedback_type": "love"}
LFM_TARGET = {"service": "lastfm"}


@pytest.fixture
async def session(db_engine):
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        yield db


# ---------------------------------------------------------------------------
# The field survives the queue
# ---------------------------------------------------------------------------


async def test_a_job_can_name_a_second_account_on_each_side(client):
    resp = await client.post(
        "/api/relay/jobs",
        json={
            "source": {"service": "listenbrainz", "account": "old"},
            "targets": [{"service": "listenbrainz", "account": "new"}],
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["source_config"] == {
        "service": "listenbrainz",
        "account": "old",
        "feedback_type": "love",
    }
    assert body["targets_config"] == [{"service": "listenbrainz", "account": "new"}]


async def test_a_config_without_an_account_names_the_first_one(client):
    resp = await client.post("/api/relay/jobs", json={"source": PLEX_SOURCE, "targets": [LFM_TARGET]})

    assert resp.status_code == 201
    # Byte-identical to what older builds stored: nothing to migrate, and an old
    # queued job is still recognised as the same relay.
    assert resp.json()["targets_config"] == [{"service": "lastfm"}]


@pytest.mark.parametrize("account", ["Old", "old account", "old:new", "-old", ""])
async def test_an_account_name_that_would_not_parse_is_refused(client, account):
    resp = await client.post(
        "/api/relay/jobs",
        json={
            "source": PLEX_SOURCE,
            "targets": [{"service": "lastfm", "account": account}],
        },
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Which pairings are accepted
# ---------------------------------------------------------------------------


async def test_an_account_cannot_relay_into_itself(client):
    resp = await client.post(
        "/api/relay/jobs",
        json={
            "source": {"service": "lastfm", "account": "old"},
            "targets": [{"service": "lastfm", "account": "old"}],
        },
    )

    assert resp.status_code == 422
    assert "both the source and a target" in resp.json()["detail"]


@pytest.mark.parametrize(
    "source, target",
    [
        ({"service": "plex", "account": "shelf"}, {"service": "plex", "account": "attic"}),
        ({"service": "plex"}, {"service": "plex", "account": "attic"}),
        ({"service": "listenbrainz", "account": "old"}, {"service": "listenbrainz", "account": "new"}),
        ({"service": "lastfm", "account": "old"}, {"service": "lastfm", "account": "new"}),
    ],
)
async def test_two_accounts_of_one_service_can_be_relayed(client, source, target):
    resp = await client.post(
        "/api/relay/jobs", json={"source": source, "targets": [target]}
    )

    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Reset names one account
# ---------------------------------------------------------------------------


async def test_a_reset_can_name_a_second_account(client):
    resp = await client.post(
        "/api/relay/jobs",
        json={
            "source": {
                "service": "reset",
                "target_service": "lastfm",
                "target_account": "old",
            },
            "targets": [],
        },
    )

    assert resp.status_code == 201
    assert resp.json()["source_config"] == {
        "service": "reset",
        "target_service": "lastfm",
        "target_account": "old",
        "all": False,
    }


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("plex", PlexToConfig(service="plex")),
        ("plex:attic", PlexToConfig(service="plex", account="attic")),
        ("lastfm:old", LastFMToConfig(service="lastfm", account="old")),
        ("listenbrainz", ListenBrainzToConfig(service="listenbrainz")),
    ],
)
def test_a_reset_targets_the_account_it_names(ref, expected):
    assert relay_core._reset_target_config(ref) == expected  # pylint: disable=protected-access


def test_a_reset_of_an_unknown_service_is_refused():
    # ``parse_ref`` names the services it knows; nothing falls through to a wipe.
    with pytest.raises(ValueError, match="unknown service"):
        relay_core._reset_target_config("spotify")  # pylint: disable=protected-access


@pytest.mark.parametrize(
    "ref, dispatch",
    [
        ("plex:attic", "reset_plex"),
        ("lastfm:old", "reset_lastfm"),
        ("listenbrainz:new", "reset_listenbrainz"),
    ],
)
async def test_a_wipe_dispatches_on_the_kind_and_passes_the_account(
    session, monkeypatch, ref, dispatch
):
    """A ref must reach its own service's wipe, carrying the account with it."""
    seen: list[str] = []

    async def fake(_db, _fetch=None, _track=None, *, ref="", **_kwargs):
        seen.append(ref)
        return 0, 0

    for name in ("reset_plex", "reset_lastfm", "reset_listenbrainz"):
        monkeypatch.setattr(relay_core, name, fake if name == dispatch else None)

    await relay_core._reset_service(  # pylint: disable=protected-access
        session, ref, None, on_track=relay_core._noop_track, dry_run=True
    )

    assert seen == [ref]


def test_a_hate_relay_cannot_target_a_second_lastfm_account():
    from ratingrelay.core.factory import pairing_refusal  # pylint: disable=import-outside-toplevel

    refusal = pairing_refusal("plex", "lastfm:new", relay="hate")
    assert refusal is not None and "no hate" in refusal