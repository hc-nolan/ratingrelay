"""Sign in to Plex from a terminal.

The browser flow (:mod:`ratingrelay.core.plex_auth`) hands the PIN to a cookie
and waits for Plex to redirect a popup back to us. A headless install has no
popup: ``ratingrelay plex-login`` prints the link, waits for the account owner to
open it on whatever device has a browser, and stores what the callback would have
stored. Same PIN flow, same client identity, same rows — a headless install and
the web app end up in exactly the same state.

Nothing here imports the web stack, so the CLI can use it (:mod:`ratingrelay.cli`
has an AST test for that). Plex calls are synchronous and blocking; the callers
are the CLI and the API's ``run_sync``, not an event loop.
"""

import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence
from urllib.parse import urlparse

from plexapi.myplex import MyPlexAccount, MyPlexPinLogin

from ratingrelay.core.plex_auth import new_pin_login

# Long enough for "find my phone, open the link, sign in", short enough that a
# forgotten command does not hang in a terminal or a shell script forever.
DEFAULT_TIMEOUT = 120
# Plex confirms within a second or two of the sign-in; polling a shared endpoint
# twice a second for two minutes is unnecessary load on someone else's servers.
POLL_SECONDS = 2.0
# One poll must not outlast the whole command. plexapi's default is 30 s per
# request, which is longer than the gap between two checks and would let a
# stalled connection push a --timeout 120 well past two minutes.
REQUEST_TIMEOUT = 10


class SignInExpired(Exception):
    """Plex refused or expired the PIN — the user's link no longer works."""


@dataclass(frozen=True, slots=True)
class ServerChoice:
    """One server the signed-in account can see, and where to reach it."""

    name: str
    url: str


def begin() -> tuple[MyPlexPinLogin, str]:
    """Create a PIN and return it with the URL a human opens to claim it."""
    pin_login = new_pin_login()
    return pin_login, pin_login.oauthUrl()


def wait_for_token(
    pin_login: MyPlexPinLogin,
    timeout: int = DEFAULT_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Poll *pin_login* until Plex confirms the sign-in, and return the token.

    Raises ``SignInExpired`` as soon as Plex says the PIN is no good — that is a
    refusal, not a slow human, and the message should say so. Raises
    ``TimeoutError`` when nothing happens within *timeout* seconds: a headless
    command must end, not wait forever on a link nobody opened. ``sleep`` and
    ``clock`` are injectable so a test does not wait in real time.

    The deadline is checked between polls, so a check already in flight can add
    up to the PIN's own request timeout on the way out.
    """
    deadline = clock() + timeout
    while True:
        if pin_login.checkLogin() and pin_login.token:
            return pin_login.token
        if pin_login.expired:
            raise SignInExpired(
                "Plex expired or refused that sign-in; run the command again "
                "for a fresh link"
            )
        if clock() >= deadline:
            raise TimeoutError(f"Plex sign-in did not complete within {timeout} seconds")
        sleep(POLL_SECONDS)


def account_servers(token: str) -> tuple[str, List[ServerChoice]]:
    """The account's name, and every *server* it can reach.

    A Plex account also lists clients and players, and a player advertises a
    connection of its own. Only a media server can be relayed to: storing a
    player's address would point every run at something with no library.
    """
    account = MyPlexAccount(token=token)
    choices = []
    for resource in account.resources():
        if resource.product != "Plex Media Server":
            continue
        url = _reachable_url(resource)
        if url:
            choices.append(ServerChoice(resource.name, url))
    return account.username, choices


def choose(
    choices: Sequence[ServerChoice],
    name: Optional[str] = None,
    url: Optional[str] = None,
) -> ServerChoice:
    """The server to store: the one named, else the one already configured.

    Nothing is guessed between several servers: a wrong one is a relay reading
    the wrong library, so an ambiguous answer lists what is available instead.
    """
    if name:
        for choice in choices:
            if choice.name.casefold() == name.casefold():
                return choice
        raise ValueError(
            f"no Plex server named {name!r}"
            + (f" (this account sees {_names(choices)})" if choices else "")
        )
    if url:
        # The deployment already names a server by address, which is what the
        # relay will use; take its name from the account rather than asking.
        host = urlparse(url).hostname
        for choice in choices:
            if urlparse(choice.url).hostname == host:
                return choice
    if len(choices) == 1:
        return choices[0]
    if not choices:
        raise ValueError("this Plex account can see no servers")
    raise ValueError(
        f"this Plex account sees {_names(choices)}; pick one with --server NAME"
    )


def _names(choices: Sequence[ServerChoice]) -> str:
    return ", ".join(choice.name for choice in choices)


def _reachable_url(resource) -> Optional[str]:
    """Where to reach *resource*: a local address first, then direct, then relay.

    The relay is last on purpose — it is the slowest path and it exists for
    servers that cannot be reached directly, not as the default.
    """
    connections = [conn for conn in getattr(resource, "connections", []) if conn.uri]
    if not connections:
        return None
    connections.sort(
        key=lambda conn: (
            not conn.local,
            bool(conn.relay),
            (conn.protocol or "").lower() != "http",
        )
    )
    return connections[0].uri
