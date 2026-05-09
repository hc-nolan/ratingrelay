"""
In-memory store for active Plex OAuth PIN login sessions.

A MyPlexPinLogin instance must remain alive while we wait for the user to
complete the OAuth flow in the popup window.  We key each session on the
integer PIN id returned by plexapi and clean it up once the token is
retrieved or the TTL is exceeded.
"""

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from plexapi.myplex import MyPlexPinLogin

log = logging.getLogger("ratingrelay")

# TTL for an unconfirmed session (plexapi default timeout is 120 s)
_SESSION_TTL = timedelta(minutes=5)

# { pin_id: (pin_login_instance, created_at) }
_sessions: dict[int, tuple[MyPlexPinLogin, datetime]] = {}


def create_session(forward_url: str) -> tuple[MyPlexPinLogin, str]:
    """
    Create a new MyPlexPinLogin OAuth session, store it, and return it along
    with the OAuth URL.  oauthUrl() must be called here because it triggers the
    PIN fetch (_getCode) that populates _id — calling .pin before that raises.
    """
    pin_login = MyPlexPinLogin(oauth=True)
    oauth_url = pin_login.oauthUrl(forwardUrl=forward_url)
    pin_id = int(pin_login._id)
    _sessions[pin_id] = (pin_login, datetime.now(tz=timezone.utc))
    log.info("Plex OAuth session created for pin_id=%s", pin_id)
    _evict_expired()
    return pin_login, oauth_url


def get_session(pin_id: int) -> MyPlexPinLogin | None:
    """Return the stored session for pin_id, or None if not found / expired."""
    entry = _sessions.get(pin_id)
    if entry is None:
        return None
    pin_login, created_at = entry
    if datetime.now(tz=timezone.utc) - created_at > _SESSION_TTL:
        log.warning("Plex OAuth session %s has expired", pin_id)
        del _sessions[pin_id]
        return None
    return pin_login


def iter_sessions() -> Iterator[tuple[int, MyPlexPinLogin]]:
    """Yield (pin_id, pin_login) for all non-expired sessions.

    Evicts stale sessions before iterating.  Safe to mutate _sessions after
    consuming this iterator (it snapshots the keys via list()).
    """
    _evict_expired()
    for pin_id, (pin_login, _) in list(_sessions.items()):
        yield pin_id, pin_login


def remove_session(pin_id: int) -> None:
    """Remove a session after it has been used."""
    _sessions.pop(pin_id, None)
    log.info("Plex OAuth session %s removed", pin_id)


def _evict_expired() -> None:
    """Remove any sessions that have exceeded their TTL."""
    cutoff = datetime.now(tz=timezone.utc) - _SESSION_TTL
    expired = [pid for pid, (_, created) in _sessions.items() if created < cutoff]
    for pid in expired:
        log.info("Evicting expired Plex OAuth session %s", pid)
        del _sessions[pid]
