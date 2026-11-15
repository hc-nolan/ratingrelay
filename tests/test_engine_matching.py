"""Engine-level tests: normalisation, the matching ladder, and typed results.

These are the first tests in the repo that cover matching at all, and they run
without a database, a network client or an app instance — which is the point of
the engine being headless.
"""

import ast
import pathlib

import pytest

from ratingrelay.engine import (
    Candidate,
    Intent,
    IntentKind,
    MatchStatus,
    MatchTier,
    OutcomeStatus,
    Reason,
    RunReport,
    TargetReport,
    Track,
    TrackOutcome,
    match_track,
    normalize,
    track_key,
    variant_form,
    variant_key,
)
from ratingrelay.engine.normalize import comparison_form


# ---------------------------------------------------------------------------
# Normalisation — the key format is a data format; changing it orphans rows
# ---------------------------------------------------------------------------


def test_normalize_keeps_v2_key_format():
    assert normalize("Artist A") == "artist a"
    assert normalize("  Don't   Stop Me Now ") == "dont stop me now"
    assert track_key("Artist A", "Song A") == "artist a||song a"


def test_track_key_is_what_sync_records_and_mappings_are_keyed_on():
    # v2 hashed the same string in core/relay.py and routes/unmatched.py.
    assert track_key("Sigur Rós", "Hoppípolla") == "sigur rós||hoppípolla"
    assert track_key("", "") == "||"


def test_comparison_form_drops_quotes_but_keeps_punctuation():
    assert comparison_form("Don’t Stop Me Now") == "dont stop me now"
    assert comparison_form("AC/DC") == "ac/dc"
    assert comparison_form("  The   Beatles ") == "the beatles"


@pytest.mark.parametrize(
    "noisy",
    [
        "Paranoid Android (Remastered 2011)",
        "Paranoid Android [Live]",
        "Paranoid Android - Remastered",
        "Paranoid Android (feat. Somebody)",
        "Paranoid Android feat. Somebody",
        # A production credit names who is on the recording, not which one it is.
        "Paranoid Android (Prod. Flying Lotus)",
        "Paranoid Android prod. Flying Lotus",
        "Paranoid Android (Produced by Flying Lotus)",
    ],
)
def test_variant_form_removes_edition_noise(noisy):
    assert variant_form(noisy) == variant_form("Paranoid Android")


@pytest.mark.parametrize("title", ["Prodigy", "Product of the 80s", "Proud"])
def test_a_credit_prefix_is_only_a_credit(title):
    """Whole words only: a title that merely starts with “prod” keeps its name."""
    assert variant_form(title) == normalize(title)


def test_variant_form_keeps_meaningful_suffixes():
    # A part number identifies a different recording; it must not be dropped.
    assert variant_form("Shine On You Crazy Diamond (Parts I-V)") != variant_form(
        "Shine On You Crazy Diamond"
    )
    assert variant_form("Song - Part 2") != variant_form("Song")


def test_variant_form_folds_accents():
    assert variant_form("Björk") == "bjork"
    assert variant_form("Hoppípolla") == "hoppipolla"
    assert variant_key("Sigur Rós", "Hoppípolla") == variant_key("Sigur Ros", "Hoppipolla")


# ---------------------------------------------------------------------------
# Ladder rungs
# ---------------------------------------------------------------------------


def _candidate(artist, title, mbid=None, external_id=None):
    return Candidate(artist=artist, title=title, recording_mbid=mbid, external_id=external_id)


def test_mbid_rung_wins_over_artist_and_title():
    source = Track(artist="Plex Name", title="Plex Title", recording_mbid="rec-1")
    other = _candidate("Different", "Different", mbid="rec-2")
    same_recording = _candidate("Real Artist", "Real Title", mbid="rec-1")
    outcome = match_track(source, [other, same_recording])
    assert outcome.status is MatchStatus.MATCHED
    assert outcome.tier is MatchTier.MBID
    assert outcome.matches == (same_recording,)


def test_exact_rung_matches_every_copy_of_the_recording():
    source = Track(artist="Nirvana", title="Lithium")
    copies = [
        _candidate("Nirvana", "Lithium", external_id="disc-1"),
        _candidate("nirvana", "lithium", external_id="disc-2"),
    ]
    outcome = match_track(source, copies)
    assert outcome.status is MatchStatus.MATCHED
    assert outcome.tier is MatchTier.EXACT
    assert len(outcome.matches) == 2


def test_variant_rung_matches_a_unique_remaster():
    source = Track(artist="Radiohead", title="Paranoid Android")
    remaster = _candidate("Radiohead", "Paranoid Android (Remastered 2011)")
    outcome = match_track(source, [remaster])
    assert outcome.status is MatchStatus.MATCHED
    assert outcome.tier is MatchTier.VARIANT
    assert outcome.matches == (remaster,)


def test_variant_rung_refuses_to_pick_between_candidates():
    source = Track(artist="Radiohead", title="Paranoid Android")
    variants = [
        _candidate("Radiohead", "Paranoid Android (Remastered 1997)"),
        _candidate("Radiohead", "Paranoid Android (Remastered 2011)"),
    ]
    outcome = match_track(source, variants)
    assert outcome.status is MatchStatus.AMBIGUOUS
    assert outcome.tier is MatchTier.VARIANT
    assert outcome.matches == ()
    assert set(outcome.candidates) == set(variants)


def test_containment_rung_handles_an_article_in_the_artist_name():
    source = Track(artist="Beatles", title="Come Together")
    candidate = _candidate("The Beatles", "Come Together")
    outcome = match_track(source, [candidate])
    assert outcome.status is MatchStatus.MATCHED
    assert outcome.tier is MatchTier.CONTAINMENT
    assert outcome.matches == (candidate,)


def test_containment_rung_refuses_to_pick_between_candidates():
    source = Track(artist="Nirvana", title="Lithium")
    candidates = [
        _candidate("Nirvana", "Lithium - 2009 Mix"),
        _candidate("Nirvana", "Lithium - 2011 Mix"),
    ]
    outcome = match_track(source, candidates)
    assert outcome.status is MatchStatus.AMBIGUOUS
    assert outcome.tier is MatchTier.CONTAINMENT
    assert outcome.matches == ()


def test_containment_ignores_an_artist_whose_name_merely_contains_the_artist():
    # "Nirvana" is a substring of "Nirvana Tribute Band"; a tribute act must
    # never be written to, so artist matching is equality after variants.
    source = Track(artist="Nirvana", title="Lithium")
    outcome = match_track(source, [_candidate("Nirvana Tribute Band", "Lithium")])
    assert outcome.status is MatchStatus.NONE
    assert outcome.matches == ()


def test_no_match_proposes_candidates_but_writes_nothing():
    source = Track(artist="Nirvana", title="Lithium")
    lookalike = _candidate("Nirvana Tribute Band", "Lithium")
    outcome = match_track(source, [lookalike])
    assert outcome.status is MatchStatus.NONE
    assert outcome.matches == ()
    assert outcome.candidates == (lookalike,)


def test_fuzzy_disabled_stops_after_exact():
    source = Track(artist="Radiohead", title="Paranoid Android")
    remaster = _candidate("Radiohead", "Paranoid Android (Remastered 2011)")
    outcome = match_track(source, [remaster], allow_fuzzy=False)
    assert outcome.status is MatchStatus.NONE
    assert outcome.matches == ()
    assert remaster in outcome.candidates


def test_missing_metadata_never_matches():
    outcome = match_track(Track(artist="", title="Song"), [_candidate("", "Song")])
    assert outcome.status is MatchStatus.NONE
    assert outcome.tier is None


def test_no_candidates_is_no_match():
    outcome = match_track(Track(artist="Nobody", title="Nothing"), [])
    assert outcome.status is MatchStatus.NONE
    assert outcome.candidates == ()


# ---------------------------------------------------------------------------
# Intents and typed results
# ---------------------------------------------------------------------------


def test_intent_keys_distinguish_state_on_the_same_track():
    keys = {
        Intent(IntentKind.LOVE).key,
        Intent(IntentKind.HATE).key,
        Intent(IntentKind.RATING, 8.0).key,
    }
    assert keys == {"love", "hate", "rating:8.0"}


def test_rating_intent_requires_a_value_and_others_reject_one():
    with pytest.raises(ValueError):
        Intent(IntentKind.RATING)
    with pytest.raises(ValueError):
        Intent(IntentKind.LOVE, 8.0)


def test_target_report_counts_come_from_outcomes():
    track = Track(artist="A", title="T")
    outcomes = (
        TrackOutcome(track, "lastfm", OutcomeStatus.WRITTEN),
        TrackOutcome(track, "lastfm", OutcomeStatus.SKIPPED, Reason.ALREADY_SYNCED),
        TrackOutcome(track, "lastfm", OutcomeStatus.UNMATCHED, Reason.AMBIGUOUS_MATCH),
        TrackOutcome(track, "lastfm", OutcomeStatus.FAILED, Reason.WRITE_FAILED),
        TrackOutcome(track, "plex", OutcomeStatus.WRITTEN),
    )
    report = TargetReport.from_outcomes("lastfm", outcomes)
    assert (report.written, report.skipped, report.unmatched, report.failed) == (1, 1, 1, 1)

    run = RunReport(
        source="plex",
        tracks_fetched=1,
        targets=(report, TargetReport.from_outcomes("plex", outcomes)),
        outcomes=outcomes,
    )
    assert run.had_errors
    assert run.status == "partial"


def test_run_report_with_no_targets_failed():
    assert RunReport(source="plex", tracks_fetched=0, targets=()).status == "failed"


# ---------------------------------------------------------------------------
# Layering guard — the engine stays headless
# ---------------------------------------------------------------------------


FORBIDDEN_IMPORTS = ("fastapi", "starlette", "sqlalchemy", "sqlmodel")


def test_engine_does_not_import_frameworks_orm_schemas_or_core():
    engine_dir = pathlib.Path("ratingrelay/engine")
    offenders = []
    for path in engine_dir.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in FORBIDDEN_IMPORTS or name.startswith(
                    ("ratingrelay.schemas", "ratingrelay.core", "ratingrelay.routes")
                ):
                    offenders.append(f"{path}: {name}")
    assert offenders == []
