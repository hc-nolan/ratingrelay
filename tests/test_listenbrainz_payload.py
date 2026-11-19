"""ListenBrainz payload shapes that only bite against the real service.

The fakes hand the pipeline already-parsed tracks, so nothing exercised the
mapping from a raw feedback row to a ``Track`` until a live run died on it.
"""

from ratingrelay.engine.services.listenbrainz import _to_track


def test_feedback_row_with_null_metadata_is_a_track_with_no_identity():
    """A row the server cannot resolve must not abort the run.

    ListenBrainz returns ``track_metadata: null`` for feedback on a recording it
    has no metadata for. Reading through the null raised ``AttributeError`` and
    took the whole relay down (RR-20).
    """

    track = _to_track({"recording_mbid": "dda6a84a-6f1c-4f2f-8f6a-1a2b3c4d5e6f", "track_metadata": None})

    assert track.artist == ""
    assert track.title == ""
    assert track.recording_mbid == "dda6a84a-6f1c-4f2f-8f6a-1a2b3c4d5e6f"


def test_feedback_row_without_metadata_key_is_a_track_with_no_identity():
    track = _to_track({"recording_mbid": "dda6a84a-6f1c-4f2f-8f6a-1a2b3c4d5e6f"})

    assert (track.artist, track.title) == ("", "")


def test_feedback_row_keeps_the_names_it_has():
    track = _to_track(
        {
            "recording_mbid": "dda6a84a-6f1c-4f2f-8f6a-1a2b3c4d5e6f",
            "track_metadata": {"artist_name": "Your Old Droog", "track_name": "Smores"},
        }
    )

    assert (track.artist, track.title) == ("Your Old Droog", "Smores")


def test_feedback_row_without_an_mbid_has_none():
    """``""`` from the API is not an identity, and must not become one."""

    track = _to_track({"recording_mbid": "", "track_metadata": None})

    assert track.recording_mbid is None
