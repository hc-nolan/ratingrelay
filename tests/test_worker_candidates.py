"""The queue's candidate payload: what a human is offered to fix a mismatch.

A row that says "no match" and offers nothing leaves the only remedy as retyping
the artist and title from memory. These tests pin that the candidates the matching
ladder already computed survive the trip to the browser, and that a ListenBrainz
candidate carries the MBID that makes it actionable.
"""

import json
from typing import Optional

from ratingrelay.core.worker import _candidates_json
from ratingrelay.engine import Candidate, OutcomeStatus, Reason, Track, TrackOutcome


def _outcome(matches: tuple[Candidate, ...] = ()) -> TrackOutcome:
    return TrackOutcome(
        track=Track("Artist A", "Song A"),
        target="listenbrainz",
        status=OutcomeStatus.UNMATCHED,
        reason=Reason.NO_RECORDING_MBID,
        detail="No MusicBrainz recording ID found",
        matches=matches,
    )


def test_no_candidates_is_null_not_an_empty_list():
    """The column stays NULL so "never proposed" and "proposed nothing" differ."""
    assert _candidates_json(None) is None
    assert _candidates_json(_outcome()) is None


def test_a_candidate_keeps_the_recording_mbid():
    """Feedback is keyed by MBID, so a name-only candidate is not a resolution."""
    payload: Optional[str] = _candidates_json(
        _outcome(
            (
                Candidate(
                    artist="Artist A", title="Song A (Live)", recording_mbid="rec-live"
                ),
                Candidate(artist="Artist A", title="Song A (Demo)"),
            )
        )
    )

    assert payload is not None
    assert json.loads(payload) == [
        {"artist": "Artist A", "title": "Song A (Live)", "recording_mbid": "rec-live"},
        {"artist": "Artist A", "title": "Song A (Demo)", "recording_mbid": None},
    ]
