"""Plex names the API client that asks for a token.

plexapi sends ``X-Plex-Product: PlexAPI`` unless told otherwise, and Plex puts
that name in front of the account owner: "PlexAPI would like to sign into your
account". That is our login screen, so it has to say our name.
"""

import pytest

from ratingrelay.core import plex_auth


class _FakePinLogin:
    """Records what create_session asks plexapi for, without touching plex.tv."""

    kwargs: dict = {}

    def __init__(self, **kwargs):
        _FakePinLogin.kwargs = kwargs
        self._id = 42

    def oauthUrl(self, forwardUrl=None):  # noqa: N802, N803 - plexapi's spelling
        return f"https://app.plex.tv/auth#?forwardUrl={forwardUrl}"


@pytest.fixture(autouse=True)
def clean_sessions():
    plex_auth._sessions.clear()
    yield
    plex_auth._sessions.clear()


def test_the_login_is_named_after_this_app(monkeypatch):
    monkeypatch.setattr(plex_auth, "MyPlexPinLogin", _FakePinLogin)

    _, url = plex_auth.create_session("http://relay.local/api/plex/auth/callback")

    assert _FakePinLogin.kwargs["headers"]["X-Plex-Product"] == "RatingRelay"
    assert "relay.local" in url


def test_the_client_identifier_is_left_to_plexapi(monkeypatch):
    """Changing it would list this app as a second device in the account."""
    monkeypatch.setattr(plex_auth, "MyPlexPinLogin", _FakePinLogin)

    plex_auth.create_session("http://relay.local/cb")

    assert "X-Plex-Client-Identifier" not in _FakePinLogin.kwargs["headers"]
