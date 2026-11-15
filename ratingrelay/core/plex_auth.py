"""
In-memory store for active Plex OAuth PIN login sessions.

A MyPlexPinLogin instance must remain alive while we wait for the user to
complete the OAuth flow in the popup window.  We key each session on the
integer PIN id returned by plexapi and clean it up once the token is
retrieved or the TTL is exceeded.

Every PIN belongs to the browser that opened it. ``init`` hands that browser a
random nonce in a short-lived cookie (:data:`PIN_COOKIE`), and only a request
carrying the same nonce can redeem the PIN. Without that, the PIN id was the
whole secret: a stranger could redeem a login the owner started (the ids are
small and sequential), or confirm their own PIN and have the owner's browser
complete it through the callback.
"""

import hmac
import logging
import secrets
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from plexapi.myplex import MyPlexAccount, MyPlexPinLogin

log = logging.getLogger("ratingrelay")

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def public_url(base_url: str, request_base_url: str, path: str) -> str:
    """Where a browser (not this process) should be sent to reach us.

    ``base_url`` is authoritative whenever it names a real address: that is the
    deployed case, and a configured answer must win. When it is still the
    localhost default, the browser is not necessarily on this machine — the dev
    server binds every interface, so the app can be open on a laptop elsewhere on
    the LAN. A localhost redirect would send that browser to its own localhost
    and the login would silently die, so fall back to the origin the request
    arrived on.
    """
    configured = base_url.rstrip("/")
    if urlparse(configured).hostname not in _LOCAL_HOSTS:
        return f"{configured}{path}"
    return f"{request_base_url.rstrip('/')}{path}"


# TTL for an unconfirmed session (plexapi default timeout is 120 s)
_SESSION_TTL = timedelta(minutes=5)

# The cookie holding the nonce that owns this browser's pending PIN. It lives as
# long as the PIN does and is only ever sent to the API.
PIN_COOKIE = "relay_pin"
_PIN_COOKIE_PATH = "/api"

# { pin_id: (pin_login_instance, created_at, nonce) }
_sessions: dict[int, tuple[MyPlexPinLogin, datetime, str]] = {}


def new_nonce() -> str:
    """A fresh owner for a PIN; goes to the browser that asked for it."""
    return secrets.token_urlsafe(32)


def set_pin_cookie(response, request, nonce: str) -> None:
    """Hand *nonce* to the browser starting the login.

    ``Lax`` because the callback is a top-level navigation back from
    app.plex.tv, which ``Strict`` would strip the cookie from.
    """
    response.set_cookie(
        PIN_COOKIE,
        nonce,
        max_age=int(_SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path=_PIN_COOKIE_PATH,
    )


def clear_pin_cookie(response) -> None:
    response.delete_cookie(PIN_COOKIE, path=_PIN_COOKIE_PATH)


def _owns(nonce: str, candidate: str | None) -> bool:
    return bool(nonce) and bool(candidate) and hmac.compare_digest(nonce, candidate)


def create_session(forward_url: str, nonce: str = "") -> tuple[MyPlexPinLogin, str]:
    """
    Create a new MyPlexPinLogin OAuth session, store it under *nonce*, and
    return it along with the OAuth URL. A PIN stored without a nonce can never
    be redeemed.  oauthUrl() must be called here because it triggers the
    PIN fetch (_getCode) that populates _id — calling .pin before that raises.
    """
    pin_login = MyPlexPinLogin(
        oauth=True,
        # plexapi calls itself "PlexAPI" in every request header, and Plex uses
        # that to name the client in the sign-in prompt: "PlexAPI would like to
        # sign into your account". The account owner is holding our app, so it
        # says our name. The client identifier is left alone — changing it
        # would show up as a second device in their Plex account.
        headers={"X-Plex-Product": "RatingRelay"},
    )
    oauth_url = pin_login.oauthUrl(forwardUrl=forward_url)
    pin_id = int(pin_login._id)
    _sessions[pin_id] = (pin_login, datetime.now(tz=timezone.utc), nonce)
    log.info("Plex OAuth session created for pin_id=%s", pin_id)
    _evict_expired()
    return pin_login, oauth_url


def get_session(pin_id: int, nonce: str | None) -> MyPlexPinLogin | None:
    """The session for *pin_id* if *nonce* owns it; None if not, or expired."""
    entry = _sessions.get(pin_id)
    if entry is None or not _owns(entry[2], nonce):
        return None
    pin_login, created_at, _ = entry
    if datetime.now(tz=timezone.utc) - created_at > _SESSION_TTL:
        log.warning("Plex OAuth session %s has expired", pin_id)
        del _sessions[pin_id]
        return None
    return pin_login


def sessions_for(nonce: str | None) -> Iterator[tuple[int, MyPlexPinLogin]]:
    """Yield (pin_id, pin_login) for the non-expired sessions *nonce* owns.

    Evicts stale sessions before iterating.  Safe to mutate _sessions after
    consuming this iterator (it snapshots the keys via list()).
    """
    _evict_expired()
    for pin_id, (pin_login, _, owner) in list(_sessions.items()):
        if _owns(owner, nonce):
            yield pin_id, pin_login


def remove_session(pin_id: int) -> None:
    """Remove a session after it has been used."""
    _sessions.pop(pin_id, None)
    log.info("Plex OAuth session %s removed", pin_id)


def _evict_expired() -> None:
    """Remove any sessions that have exceeded their TTL."""
    cutoff = datetime.now(tz=timezone.utc) - _SESSION_TTL
    expired = [pid for pid, (_, created, _) in _sessions.items() if created < cutoff]
    for pid in expired:
        log.info("Evicting expired Plex OAuth session %s", pid)
        del _sessions[pid]
