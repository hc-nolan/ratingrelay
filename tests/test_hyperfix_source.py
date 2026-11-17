"""Hyperfix as a source: the feed adapter, its scale mapping and the pairing rule (HF-182).

The adapter talks to ``GET /api/music/ratings`` through an injected ``httpx`` transport, so
nothing here needs a hyperfix instance.
"""

from typing import Any, Optional

import httpx
import pytest

from ratingrelay.core.factory import (
    SOURCE_SERVICES,
    TARGET_SERVICES,
    pairing_refusal,
    relay_identity,
)
from ratingrelay.engine.credentials import HyperfixCredentials
from ratingrelay.engine.refs import KNOWN_SERVICES, default_label
from ratingrelay.engine.services.hyperfix import (
    ZERO_RATING,
    HyperfixClient,
    HyperfixError,
    HyperfixSource,
)
from ratingrelay.schemas.relay import FromConfig

CREDS = HyperfixCredentials(base_url="https://hyperfix.test", token="hfx_key")


def _client(*pages: dict, seen: Optional[list[dict]] = None) -> HyperfixClient:
    """A client answering each request with the next page, recording the calls."""
    queue = list(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer hfx_key"
        assert request.url.path == "/api/music/ratings"
        if seen is not None:
            seen.append(dict(request.url.params))
        return httpx.Response(200, json=queue.pop(0) if queue else {"items": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HyperfixClient(CREDS, page_size=2, http=http)


def _item(mbid: str, rating: Any, artist: str = "Wire", title: str = "Field Day") -> dict:
    return {
        "recording_mbid": mbid,
        "rating": rating,
        "artist": artist,
        "title": title,
        "release_mbid": "release-1",
    }


def test_the_kind_is_a_source_and_not_a_target():
    assert "hyperfix" in KNOWN_SERVICES
    assert default_label("hyperfix") == "Hyperfix"
    assert "hyperfix" in SOURCE_SERVICES
    assert "hyperfix" not in TARGET_SERVICES


@pytest.mark.anyio
async def test_the_client_maps_the_scale_and_walks_every_page():
    seen: list[dict] = []
    client = _client(
        {"items": [_item("r1", 90), _item("r2", 20)], "next_cursor": "c2"},
        {"items": [_item("r3", 80, title="Ex-Lion Tamer")], "next_cursor": None},
        seen=seen,
    )
    tracks = await client.ratings()
    assert [(t.recording_mbid, t.rating) for t in tracks] == [("r1", 9.0), ("r2", 2.0), ("r3", 8.0)]
    assert (tracks[0].artist, tracks[0].title) == ("Wire", "Field Day")
    assert seen[0] == {"limit": "2"}
    assert seen[1]["cursor"] == "c2"


@pytest.mark.anyio
async def test_a_zero_rating_is_not_unrated():
    """hyperfix's 0 is a real rating; this engine reads 0.0 as unrated, so it is raised."""
    client = _client({"items": [_item("r1", 0)], "next_cursor": None})
    tracks = await client.ratings()
    assert [t.rating for t in tracks] == [ZERO_RATING]


@pytest.mark.anyio
async def test_the_direction_filters_at_the_threshold():
    client = _client(
        {"items": [_item("high", 90), _item("mid", 50), _item("low", 10)], "next_cursor": None}
    )
    loves = await HyperfixSource(client, 8.0, "gte").fetch()
    assert [t.recording_mbid for t in loves] == ["high"]
    hates = await HyperfixSource(
        _client({"items": [_item("high", 90), _item("low", 10)], "next_cursor": None}),
        2.0,
        "lte",
    ).fetch()
    assert [t.recording_mbid for t in hates] == ["low"]


@pytest.mark.anyio
async def test_a_refused_key_is_an_error_not_an_empty_library():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(HyperfixError, match="401"):
        await HyperfixClient(CREDS, http=http).ratings()


@pytest.mark.anyio
async def test_a_row_without_an_artist_or_title_fails_the_run():
    client = _client({"items": [_item("r1", 90, artist="  ")], "next_cursor": None})
    with pytest.raises(HyperfixError, match="artist"):
        await client.ratings()


@pytest.mark.anyio
async def test_a_repeated_cursor_is_refused_instead_of_looping():
    cursor = {"items": [_item("r1", 90)], "next_cursor": "same"}
    client = _client(cursor, cursor, cursor)
    with pytest.raises(HyperfixError, match="repeated a cursor"):
        await client.ratings()


def test_a_hyperfix_source_pairing_cannot_write_to_plex():
    """hyperfix pushes Plex ratings itself; two reconcilers would undo each other."""
    assert pairing_refusal("hyperfix", "plex", relay="love")
    assert pairing_refusal("hyperfix", "lastfm", relay="love") is None
    assert pairing_refusal("hyperfix", "listenbrainz", relay="hate") is None
    # The rule is about the kinds, so a second Plex account is refused too.
    assert pairing_refusal("hyperfix", "plex:attic", relay="love")


def test_the_relay_identity_is_the_direction():
    assert relay_identity("hyperfix", feedback_type="love") == "love"
    assert relay_identity("hyperfix", feedback_type="hate") == "hate"


def test_a_hyperfix_source_config_is_accepted():
    config = _parse_source({"service": "hyperfix", "feedback_type": "hate"})
    assert (config.service, config.feedback_type) == ("hyperfix", "hate")


def test_a_hyperfix_target_config_is_refused():
    from pydantic import TypeAdapter, ValidationError  # pylint: disable=import-outside-toplevel

    from ratingrelay.schemas.relay import ToConfig  # pylint: disable=import-outside-toplevel

    with pytest.raises(ValidationError):
        TypeAdapter(ToConfig).validate_python({"service": "hyperfix"})


def _parse_source(payload: dict):
    from pydantic import TypeAdapter  # pylint: disable=import-outside-toplevel

    return TypeAdapter(FromConfig).validate_python(payload)


def test_a_relay_cannot_be_configured_to_write_into_hyperfix(tmp_path):
    """A target of this kind is refused where the config is read, not mid-run."""
    from ratingrelay.cli.config import ConfigError, load_relays  # pylint: disable=import-outside-toplevel

    path = tmp_path / "relays.toml"
    path.write_text('[[relay]]\nname = "back"\nsource = "lastfm"\ntargets = ["hyperfix"]\n')
    with pytest.raises(ConfigError, match="cannot be a target"):
        load_relays(str(path))


def test_the_cli_refuses_a_hyperfix_target_flag():
    from ratingrelay.cli.main import build_parser  # pylint: disable=import-outside-toplevel

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["relay", "--source", "lastfm", "--target", "hyperfix"])


def test_hyperfix_is_still_accepted_as_a_source(tmp_path):
    from ratingrelay.cli.config import load_relays  # pylint: disable=import-outside-toplevel
    from ratingrelay.cli.main import build_parser  # pylint: disable=import-outside-toplevel

    path = tmp_path / "relays.toml"
    path.write_text('[[relay]]\nname = "out"\nsource = "hyperfix"\ntargets = ["lastfm"]\n')
    assert load_relays(str(path))[0].source == "hyperfix"
    args = build_parser().parse_args(["relay", "--source", "hyperfix", "--target", "lastfm"])
    assert args.source == "hyperfix"
