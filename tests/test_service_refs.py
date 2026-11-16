"""A second account of a service, addressed by its ref.

The account that already existed keeps the bare kind (``lastfm``) forever, and a
second one is ``lastfm:old``. Those strings are what sync records, mappings and
job configs already hold, so adding an account changes no history — and two
accounts of one kind must never reconcile against each other's writes.

These tests pin the parts that make that true: the ref grammar, the pairing
rules (same account refused, same kind allowed), the credential lookup, and the
fact that a sync record under one ref is invisible under the other.
"""

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ratingrelay.core import factory
from ratingrelay.core.credentials import lastfm_credentials
from ratingrelay.db.credentials import (
    delete_credential,
    get_account,
    get_credential,
    list_accounts,
    upsert_credential,
)
from ratingrelay.db.store import SqlMappingStore, SqlSyncStore
from ratingrelay.engine.refs import (
    ServiceRef,
    account_ref,
    is_primary,
    mapping_scopes,
    parse_ref,
    service_kind,
)
from ratingrelay.engine.store import SyncedTrack
from ratingrelay.models.mappings import TrackMapping as TrackMappingRow
from ratingrelay.models.sync import SyncRecord
from ratingrelay.settings import Settings


@pytest.fixture
async def session(db_engine):
    maker = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        yield db


# ---------------------------------------------------------------------------
# The ref itself
# ---------------------------------------------------------------------------


def test_a_bare_kind_refs_the_account_that_already_exists():
    ref = ServiceRef("lastfm")
    assert ref.ref == "lastfm"
    assert ref.is_primary


def test_a_second_account_adds_its_name_to_the_kind():
    assert account_ref("lastfm", "old") == "lastfm:old"
    assert not ServiceRef("lastfm", "old").is_primary


@pytest.mark.parametrize("value", ["lastfm", "plex:attic", "listenbrainz:new-account"])
def test_a_ref_round_trips(value):
    assert parse_ref(value).ref == value


@pytest.mark.parametrize(
    "value",
    [
        "lastfm:",  # says there is an account, not which
        "lastfm:Old",  # refs appear in URLs and TOML; uppercase would need quoting
        "lastfm:a:b",  # one separator
        "lastfm:old account",  # no spaces
        "spotify",  # not a service we speak
        "",
    ],
)
def test_a_ref_that_cannot_be_read_is_refused(value):
    with pytest.raises(ValueError):
        parse_ref(value)


def test_an_account_name_is_bounded():
    with pytest.raises(ValueError, match="at most"):
        account_ref("lastfm", "a" * 33)


def test_the_kind_behind_a_ref_is_still_the_service():
    """Last.fm has no hates however the account is named."""
    assert service_kind("lastfm:old") == "lastfm"
    assert is_primary("plex")
    assert not is_primary("plex:attic")


def test_mappings_are_looked_up_most_specific_first():
    assert mapping_scopes("lastfm:old") == ("lastfm:old", "lastfm", "all")
    # The first account is its kind, so there is no second scope to try.
    assert mapping_scopes("lastfm") == ("lastfm", "all")


# ---------------------------------------------------------------------------
# Which pairings are allowed
# ---------------------------------------------------------------------------


def test_an_account_cannot_relay_into_itself():
    assert "both the source and a target" in factory.pairing_refusal(
        "plex", "plex", relay="love"
    )


@pytest.mark.parametrize(
    "source, target",
    [
        ("plex", "plex:attic"),  # a second server
        ("listenbrainz", "listenbrainz:new"),  # moving to a new account
        ("lastfm", "lastfm:new"),
        (("lastfm:old"), "lastfm"),  # and the other way round
    ],
)
def test_a_second_account_of_the_same_service_can_be_relayed_to(source, target):
    assert factory.pairing_refusal(source, target, relay="love") is None


def test_a_hate_relay_still_cannot_reach_a_lastfm_account():
    refusal = factory.pairing_refusal("plex", "lastfm:new", relay="hate")
    assert refusal is not None and "no hate" in refusal


# ---------------------------------------------------------------------------
# Building a source or target for one account
# ---------------------------------------------------------------------------


async def test_a_source_is_built_from_the_account_its_ref_names(monkeypatch, session):
    asked: list[str] = []

    async def credentials(_db, service="lastfm"):
        asked.append(service)
        return f"creds:{service}"

    monkeypatch.setattr(factory, "lastfm_credentials", credentials)
    monkeypatch.setattr(factory, "LastFmClient", lambda credentials: credentials)

    first = await factory.source_for_service(session, "lastfm")
    second = await factory.source_for_service(session, "lastfm:old")

    assert (first.service, second.service) == ("lastfm", "lastfm:old")
    assert asked == ["lastfm", "lastfm:old"]


class _Config(BaseModel):
    """The request config shape, without an ``account`` field yet."""
    service: str


def test_a_request_without_an_account_names_the_first_one():
    from ratingrelay.core.adapters import ref_for  # pylint: disable=import-outside-toplevel

    assert ref_for(_Config(service="lastfm")) == "lastfm"
    assert ref_for(_Config(service="listenbrainz")) == "listenbrainz"


# ---------------------------------------------------------------------------
# Two accounts never reconcile against each other
# ---------------------------------------------------------------------------


async def test_writes_to_two_accounts_of_one_service_are_not_shared(session):
    store = SqlSyncStore(session, "love")
    await store.remember(
        "plex", "lastfm", SyncedTrack(key="first", intent="love", target_key='["a","b"]')
    )
    await store.remember(
        "plex",
        "lastfm:old",
        SyncedTrack(key="second", intent="love", target_key='["c","d"]'),
    )

    assert set(await store.load("plex", "lastfm")) == {"first"}
    assert set(await store.load("plex", "lastfm:old")) == {"second"}


async def test_an_unclaimed_record_is_adopted_only_by_its_own_account(session):
    """A row written before relays were told apart still belongs to one pair."""
    session.add(
        SyncRecord(
            source_service="plex",
            target_service="lastfm:old",
            relay="",
            normalized_key="legacy",
            intent="love",
        )
    )
    await session.commit()

    store = SqlSyncStore(session, "love")
    assert await store.load("plex", "lastfm") == {}
    # And the account it does belong to claims it, once.
    assert set(await store.load("plex", "lastfm:old")) == {"legacy"}
    row = (await session.execute(select(SyncRecord))).scalars().one()
    assert row.relay == "love"


# ---------------------------------------------------------------------------
# Credentials, by ref
# ---------------------------------------------------------------------------


async def test_the_first_account_keeps_the_bare_ref(session):
    account = await upsert_credential(
        session, "lastfm", token="key", secret="secret", username="tester"
    )
    assert (account.id, account.service.value) == ("lastfm", "lastfm")


async def test_a_second_account_takes_its_name_as_the_default_label(session):
    account = await upsert_credential(session, "lastfm:old", token="key")
    assert account.id == "lastfm:old"
    assert account.label == "old"


async def test_listing_accounts_can_be_limited_to_one_kind(session):
    await upsert_credential(session, "lastfm", token="a")
    await upsert_credential(session, "lastfm:old", token="b")
    await upsert_credential(session, "listenbrainz", token="c")

    assert [a.id for a in await list_accounts(session, "lastfm")] == [
        "lastfm",
        "lastfm:old",
    ]
    assert len(await list_accounts(session)) == 3


async def test_a_removed_account_is_no_longer_a_credential_but_keeps_its_ref(session):
    await upsert_credential(
        session, "lastfm:old", token="key", secret="s", username="tester"
    )

    assert await delete_credential(session, "lastfm:old") is True

    assert await get_credential(session, "lastfm:old") is None
    tombstone = await get_account(session, "lastfm:old")
    assert tombstone is not None and tombstone.deleted_at is not None
    # The row exists to hold the ref, not a secret: disconnecting means gone.
    assert (tombstone.token, tombstone.secret, tombstone.username) == ("", None, None)


async def test_a_removed_ref_cannot_be_reconnected(session):
    await upsert_credential(session, "lastfm:old", token="key")
    await delete_credential(session, "lastfm:old")

    with pytest.raises(ValueError, match="different name"):
        await upsert_credential(session, "lastfm:old", token="new-key")


async def test_settings_describe_the_first_account_only(monkeypatch, session):
    """An environment variable cannot name a second account."""
    monkeypatch.setattr(
        "ratingrelay.core.credentials.get_settings",
        lambda: Settings(_env_file=None, lastfm_token="from-settings"),
    )
    await upsert_credential(
        session, "lastfm", token="row", secret="s", username="u", password_hash="h"
    )
    await upsert_credential(
        session,
        "lastfm:old",
        token="row-old",
        secret="s",
        username="u",
        password_hash="h",
    )

    first = await lastfm_credentials(session, "lastfm")
    second = await lastfm_credentials(session, "lastfm:old")

    assert first.api_key == "from-settings"
    assert second.api_key == "row-old"


# ---------------------------------------------------------------------------
# Mappings reach a second account without being re-created
# ---------------------------------------------------------------------------


async def test_a_kind_wide_mapping_applies_to_a_second_account(session):
    session.add_all(
        [
            TrackMappingRow(
                source_normalized_key="kind",
                source_artist="a",
                source_title="kind",
                mapped_artist="A",
                mapped_title="Kind",
                target_service="lastfm",
            ),
            TrackMappingRow(
                source_normalized_key="any",
                source_artist="a",
                source_title="any",
                mapped_artist="A",
                mapped_title="Any",
                target_service="all",
            ),
        ]
    )
    await session.commit()

    old = await SqlMappingStore(session).load("lastfm:old")
    assert set(old) == {"kind", "any"}
    # The first account is the kind, so it sees the same rows — as before.
    assert set(await SqlMappingStore(session).load("lastfm")) == {"kind", "any"}


async def test_the_accounts_own_mapping_wins_over_its_kinds(session):
    session.add_all(
        [
            TrackMappingRow(
                source_normalized_key="key",
                source_artist="a",
                source_title="t",
                mapped_artist="Kind",
                mapped_title="Kind",
                target_service="lastfm",
            ),
            TrackMappingRow(
                source_normalized_key="key",
                source_artist="a",
                source_title="t",
                mapped_artist="Account",
                mapped_title="Account",
                target_service="lastfm:old",
            ),
        ]
    )
    await session.commit()

    old = await SqlMappingStore(session).load("lastfm:old")
    assert old["key"].artist == "Account"
    assert (await SqlMappingStore(session).load("lastfm"))["key"].artist == "Kind"
