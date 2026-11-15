"""Sessions, signed against the Plex account the relay already syncs as.

There is exactly one identity here: the owner of the library. RatingRelay has
no password store, no user table and no second source of truth — it asks Plex
who you are, the way Overseerr and Tautulli do, and remembers the answer in a
signed cookie.

The signature key is ``settings.secret`` mixed with the Plex token:

- the default ``secret`` stops being a footgun, because the token is per-install
  and already secret, so a forged cookie still needs the database;
- rotating the Plex token invalidates every session, which is the right blast
  radius for "the account changed".

Nothing in here touches the engine: it is a property of the HTTP surface.
"""

import hashlib
import hmac
import logging
import time
from typing import Optional

from fastapi.responses import JSONResponse
from plexapi.myplex import MyPlexAccount
from sqlalchemy.ext.asyncio import AsyncSession

from ratingrelay.db import session_maker

from ratingrelay.db.credentials import get_credential
from ratingrelay.engine.thread import run_sync
from ratingrelay.models.services import ServiceName
from ratingrelay.settings import get_settings

SESSION_COOKIE = "relay_session"
SESSION_MAX_AGE = 30 * 24 * 60 * 60  # 30 days


def owner_token_stored(settings_secret: str, token: str) -> str:
    """The signing material for a given install and Plex token."""
    return f"{settings_secret}\x00{token}"


def _key_material(plex_token: str) -> str:
    return owner_token_stored(get_settings().secret, plex_token)


def _mac(plex_token: str, payload: str) -> str:
    return hmac.new(
        _key_material(plex_token).encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def issue_session(plex_user_id: str, plex_token: str) -> str:
    """Build the cookie value: ``v1.<expires>.<user id>.<mac>``."""
    expires = int(time.time()) + SESSION_MAX_AGE
    payload = f"v1.{expires}.{plex_user_id}"
    return f"{payload}.{_mac(plex_token, payload)}"


def verify_session(cookie: Optional[str], plex_token: str) -> Optional[str]:
    """Return the Plex user id if *cookie* is a valid, unexpired session."""
    if not cookie:
        return None
    parts = cookie.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        return None
    version, expires, user_id, mac = parts
    payload = f"{version}.{expires}.{user_id}"
    if not hmac.compare_digest(mac, _mac(plex_token, payload)):
        return None
    try:
        if int(expires) < time.time():
            return None
    except ValueError:
        return None
    return user_id


# ---------------------------------------------------------------------------
# The owner, and who Plex says you are
# ---------------------------------------------------------------------------


async def owner_token(db: AsyncSession) -> Optional[str]:
    """The Plex token the relay syncs as — settings first, then the DB row."""
    settings = get_settings()
    if settings.plex_token:
        return settings.plex_token
    cred = await get_credential(db, ServiceName.plex)
    return cred.token if cred else None


async def owner_identity(db: AsyncSession) -> Optional[tuple[str, str]]:
    """``(plex_user_id, username)`` for the owner, or None if not set up."""
    token = await owner_token(db)
    if not token:
        return None
    return await identify(token)


async def identify(token: str) -> Optional[tuple[str, str]]:
    """Ask Plex who a token belongs to."""
    try:
        account = await run_sync(lambda: MyPlexAccount(token=token))
    except Exception:  # pylint: disable=broad-exception-caught
        # Any failure here means the same thing: this is not a valid account.
        return None
    if account is None:
        return None
    return str(account.id), account.username


async def auth_is_configured(db: AsyncSession) -> bool:
    """True when there is an owner to protect, so requests must be signed in."""
    return await owner_token(db) is not None


# Always reachable without a session: the login itself. Everything else under
# /api needs one once an owner is configured.
OPEN_PREFIXES = ("/api/auth/",)


def session_guard(open_prefixes: tuple[str, ...] = OPEN_PREFIXES, session_factory=None):
    """Build the middleware that refuses unsigned API calls.

    This is the whole guard, and it protects *state*, not the SPA shell: a
    stranger can load the page and see that it is a login screen, but cannot
    read the library, queue a run or reset a service.

    Three deliberate holes:

    - ``/api/auth/*`` is open, or nobody could log in;
    - before a Plex account is configured there is no owner to be, so setup has
      to work — that is a fresh install, not a hole;
    - ``auth = false`` in the config turns it off entirely, for a relay that is
      only reachable from its own machine.
    """

    async def guard(request, call_next):
        path = request.url.path
        if (
            request.method == "OPTIONS"
            or not path.startswith("/api")
            or path.startswith(open_prefixes)
            or not get_settings().auth
        ):
            return await call_next(request)

        factory = session_factory or _default_session_factory()
        async with factory() as db:
            token = await owner_token(db)
        if token is None:
            return await call_next(request)

        cookie = request.cookies.get(SESSION_COOKIE)
        if verify_session(cookie, token) is None:
            _log_refusal(request, path, cookie)
            return JSONResponse(status_code=401, content={"detail": "Not signed in"})
        return await call_next(request)

    return guard


# "The browser sent no cookie" and "the browser sent a cookie we cannot verify"
# are different bugs with different fixes, and both are invisible from the page,
# so say which one happened — once per kind per process, not per request.
_REFUSALS_SEEN: set[str] = set()


def _log_refusal(request, path: str, cookie: Optional[str]) -> None:
    log = logging.getLogger("ratingrelay")
    kind = "no-cookie" if not cookie else "bad-cookie"
    log.debug("Refused %s %s (%s)", request.method, path, kind)
    if kind in _REFUSALS_SEEN:
        return
    _REFUSALS_SEEN.add(kind)
    if kind == "no-cookie":
        log.warning(
            "Refused %s %s: the request carried no session cookie at all. Either "
            "this browser never stored it, or it is on a different host than the "
            "login (cookies do not cross hosts).",
            request.method,
            path,
        )
    else:
        log.warning(
            "Refused %s %s: a session cookie was present but did not verify (%s). "
            "If the Plex token was rotated, sign in again.",
            request.method,
            path,
            _cookie_shape(cookie or ""),
        )


def _cookie_shape(cookie: str) -> str:
    """Enough of a cookie's structure to tell truncation from a bad signature."""
    parts = cookie.split(".")
    shape = f"len={len(cookie)} parts={len(parts)}"
    if len(parts) == 4 and parts[1].isdigit():
        shape += f" expires_in={int(parts[1]) - int(time.time())}s"
    return shape


def _default_session_factory():
    return session_maker
