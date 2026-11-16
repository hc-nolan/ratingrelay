"""A deadline on every request liblistenbrainz and musicbrainzngs make.

Neither library takes a timeout, and both pass none down, so a server that
accepts the connection and then never answers blocks the calling thread forever
(RR-11, GitHub #24). A hung run is worse than a failed one: a scheduler cannot
tell it apart from a slow one, and a job never reaches a track boundary where
it could be stopped. plexapi (30 s) and pylast (5 s connect, 20 s read) bound
their own requests already.

A request that times out raises an ``OSError`` subclass, so the engine's
``retry.attempt`` treats it as the transport failure it is.

The fix sits where each library builds its transport, because there is no
public setting: liblistenbrainz mounts a module-level ``requests`` adapter on
every session, and musicbrainzngs builds a urllib opener per request through
``musicbrainzngs.compat.build_opener``. ``install()`` is idempotent and is
called on import by the service modules that use each library.
"""

import urllib.request

import liblistenbrainz.client as lbz_client
from musicbrainzngs import compat as mbz_compat
from requests.adapters import HTTPAdapter

#: Seconds to wait for a connection. A reachable server answers in well under this.
CONNECT_TIMEOUT = 10.0

#: Seconds to wait between bytes of an answer. MusicBrainz searches and a page of
#: ListenBrainz feedback both come back in a second or two when the service is up.
READ_TIMEOUT = 30.0


class _TimeoutAdapter(HTTPAdapter):
    """liblistenbrainz's adapter, with a timeout when the caller gave none."""

    def send(self, request, *args, **kwargs):
        # requests.Session passes timeout by keyword, None when the caller gave none.
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (CONNECT_TIMEOUT, READ_TIMEOUT)
        return super().send(request, *args, **kwargs)


class _TimeoutHandler(urllib.request.BaseHandler):
    """Gives every musicbrainzngs request a timeout before urllib opens it.

    urllib applies ``req.timeout`` to the connection; musicbrainzngs never sets
    one, so it would be the socket default, which is no timeout at all.
    """

    def http_request(self, req):
        """Pre-process an http request: attach the timeout."""
        req.timeout = READ_TIMEOUT
        return req

    https_request = http_request


def _build_opener_with_timeout(*handlers):
    return urllib.request.build_opener(*handlers, _TimeoutHandler())


def install() -> None:
    """Bound both libraries' requests. Safe to call more than once."""
    if not isinstance(lbz_client.adapter, _TimeoutAdapter):
        lbz_client.adapter = _TimeoutAdapter(max_retries=lbz_client.retry_strategy)
    mbz_compat.build_opener = _build_opener_with_timeout
