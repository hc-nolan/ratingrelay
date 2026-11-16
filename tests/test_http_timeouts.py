"""A silent server fails a ListenBrainz or MusicBrainz call; it does not hang it (RR-11).

Both libraries pass no timeout, so before ``engine.http_timeouts`` a server that
accepted the connection and never answered blocked the run forever. The server
here is a local socket that accepts and says nothing — no real service is
touched.
"""

import functools
import socket
import threading

import musicbrainzngs.musicbrainz as mbz_module
import pytest
from liblistenbrainz import ListenBrainz

from ratingrelay.engine import http_timeouts
from ratingrelay.engine.services import musicbrainz

#: Long enough for the libraries' own retries at the shortened timeout, short
#: enough that a regression fails the suite instead of stalling it.
DEADLINE = 15


@pytest.fixture
def silent_server():
    """Accepts every connection and never sends a byte. Yields its address."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(32)
    held = []
    stop = threading.Event()

    def accept():
        while not stop.is_set():
            try:
                held.append(server.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    yield f"127.0.0.1:{server.getsockname()[1]}"
    stop.set()
    server.close()
    for conn in held:
        conn.close()


@pytest.fixture
def short_timeouts(monkeypatch):
    http_timeouts.install()
    monkeypatch.setattr(http_timeouts, "CONNECT_TIMEOUT", 0.2)
    monkeypatch.setattr(http_timeouts, "READ_TIMEOUT", 0.2)


def _outcome_within(call, seconds=DEADLINE):
    """Run *call* on a thread; return what it raised, or fail if it is still running."""
    result = {}

    def run():
        try:
            call()
            result["raised"] = None
        except Exception as exc:
            result["raised"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), f"still waiting after {seconds}s: no timeout applied"
    return result["raised"]


def test_a_silent_listenbrainz_fails_as_a_transport_error(silent_server, short_timeouts):
    client = ListenBrainz(api_base_url=f"http://{silent_server}")

    raised = _outcome_within(lambda: client.get_user_feedback("someone", 1, True, 100, 0))

    # OSError is what engine.retry.attempt retries and what fails the run loudly.
    assert isinstance(raised, OSError)


def test_a_silent_musicbrainz_fails_as_a_transport_error(
    silent_server, short_timeouts, monkeypatch
):
    monkeypatch.setattr(mbz_module, "hostname", silent_server)
    monkeypatch.setattr(mbz_module, "https", False)
    # musicbrainzngs retries a timeout eight times with growing sleeps; one try is
    # enough to show the timeout fires.
    monkeypatch.setattr(
        mbz_module, "_safe_read", functools.partial(mbz_module._safe_read, max_retries=1)
    )

    raised = _outcome_within(lambda: musicbrainz._lookup(None, "Artist", "Title"))

    assert isinstance(raised, ConnectionError)


def test_install_is_idempotent():
    http_timeouts.install()
    adapter = http_timeouts.lbz_client.adapter
    http_timeouts.install()

    assert http_timeouts.lbz_client.adapter is adapter
