# 013 — Integration tests for the relay pipeline

## Problem

RR-12 (github #26) asks for an integration suite: tests that call the relay entry
point directly instead of through HTTP, and that prove sync behaviour end to end.
The ticket as written has tests hitting **real** Plex, Last.fm and ListenBrainz
accounts with dedicated test credentials.

Two things have changed since it was filed:

1. **The entry point moved.** `core/relay.execute_relay()` still exists and is still
   what the job worker calls, but the decisions moved into `engine/pipeline.py`, and
   the clients a run touches are behind `engine/services/`.
2. **Live accounts are the wrong seam.** `AGENTS.md` says tests never touch a real
   service, and the reason is not theoretical: the v1 suite pointed at live accounts
   and wiped real ratings. A suite that un-loves and un-rates on real accounts is a
   destructive tool guarded only by its own correctness — exactly the risk we refuse
   for the relay itself.

## Decision

Fake the network, not the engine. The suite drives the **real** entry point with the
**real** adapters, the real factory, and the real SQL stores over in-memory SQLite,
and replaces only the three library objects each adapter is handed, plus the
MusicBrainz lookup. No HTTP, no browser, no account.

```
execute_relay(db, config, targets)      ← the worker's own entry point
  core/adapters ─ core/factory          ← real: relay identity, thresholds, stores
    engine/pipeline                     ← real: plan, commit, reconcile, undo, dry run
      engine/services/{plex,lastfm,listenbrainz}  ← real: matching, MBID ladder, intents
        ⟂ fakes: plexapi PlexServer, pylast network+Track,
                 liblistenbrainz client, factory.resolve_recording_mbid
      db/store (SqlSyncStore, SqlMappingStore, SqlMbidCache)  ← real, in-memory SQLite
```

Each fake keeps state a later run reads back (ratings, loves, feedback scores), which
is what makes "love, sync, unlove at the source, sync again" one test instead of an
assertion about a mock's call log.

### What each fake mirrors

| Fake | Mirrors | Detail that matters |
| --- | --- | --- |
| `FakePlex` (and `FakeSection`, `FakePlexTrack`) | `plexapi.server.PlexServer`, `library.section()`, a track | `searchTracks(**filters)` applies `userRating>>=` / `userRating<<=` **server-side** (contract §5) and raises `AssertionError` — not `BadRequest` — for anything else, so a broken filter cannot hide behind the adapter's fetch-everything fallback; `<<=` includes unrated copies, so the adapter's own guard is exercised; `rate(None)` clears and an out-of-range rating raises, as `plexapi` does |
| `FakeLastFm` (`_FakeNetwork`, `_FakeLastFmTrack`) | `pylast.LastFMNetwork`, `pylast.Track` | `love()`/`unlove()` mutate the account's loved set; `get_loved_tracks(limit=None)` returns it |
| `FakeListenBrainz` (`_FakeListenBrainzClient`) | `liblistenbrainz.ListenBrainz` | feedback keyed by recording MBID, paged by `count`/`offset`; `track_metadata` only when the caller asks for it, as the API does; `submit_user_feedback(0, mbid)` deletes, exactly as LBZ treats a neutral score |
| `FakeMusicBrainz` | `resolve_recording_mbid` | the `tid:<track mbid>` shortcut first, then exact artist+title; a miss is an empty `Resolution`, mirroring the lookup's deliberate refusal to guess. Proposals are not modelled |

`WRITE_DELAY_SECONDS` is zeroed in the fixture: the 0.3s Last.fm pacing is a rate-limit
behaviour, not sync semantics, and sleeping through it would only slow the suite.

## Scenario map

Every checkbox in RR-12, and where it is answered. `tests/integration/` unless noted.

| Scenario | Test | Approach |
| --- | --- | --- |
| Plex → LBZ loves | `test_plex_source.py::test_a_loved_track_is_loved_on_listenbrainz` | score 1 written; the server-side filter and the `tid:` MBID lookup are asserted, not just the outcome |
| Plex → LBZ hates | `…::test_a_hated_track_is_hated_on_listenbrainz` | hate relay writes -1 |
| Plex → LBZ unloving | `…::test_withdrawing_the_love_clears_listenbrainz` | second run sends score 0, record forgotten |
| Plex → LBZ unhating | `…::test_withdrawing_the_hate_clears_listenbrainz` | ditto at -1 |
| Plex → LBZ re-love on target drift | `…::test_target_drift_is_not_reapplied_while_the_record_stands` | **diverges, see below** |
| Plex → LFM loves | `…::test_a_loved_track_is_loved_on_lastfm` | loved set grows once |
| Plex → LFM unloving | `…::test_withdrawing_the_love_unloves_on_lastfm` | un-loved, record forgotten |
| Plex → LFM re-love on drift | `…::test_a_drifted_lastfm_love_is_not_reapplied` | **diverges, see below** |
| LFM love the user made by hand | `…::test_a_love_the_user_made_is_left_alone` | `SKIPPED / already_on_target`, nothing recorded (contract §3) |
| LBZ → Plex loves | `test_external_source.py::test_a_loved_recording_rates_plex` | rating written at the love threshold; `target_key` and `prior` recorded |
| LBZ → Plex hates | `…::test_a_hated_recording_rates_plex_low` | hate threshold |
| LBZ unlove clears Plex | `…::test_withdrawing_the_love_clears_the_plex_rating` | prior (`None`) restored |
| LFM → Plex loves | `…::test_a_loved_track_rates_plex` | the pair's other direction |
| LFM unlove clears Plex | `…::test_unloving_on_lastfm_clears_the_plex_rating` | prior restored |
| A rating the user chose | `…::test_a_rating_the_user_chose_is_kept_and_restored` | 4 is overwritten, then put back — never cleared |
| Apostrophe / smart quote mismatch | `test_edge_cases.py::test_smart_quotes_do_not_stop_a_match` | `Don't Stop` finds `Don’t Stop` |
| Ampersands | `…::test_an_ampersand_is_not_a_mismatch` | an artist written with `&` on one side and without on the other lines up |
| `and` vs `&` | `…::test_and_is_not_the_same_as_an_ampersand` | **unmatched**, pinned as a known limitation rather than guessed at |
| Two plausible copies | `…::test_two_plausible_copies_go_to_the_queue_with_their_candidates` | `AMBIGUOUS_MATCH` with both candidates attached, nothing written (contract §6) |
| Unrated copy on a hate read | `…::test_an_unrated_copy_is_not_hated` | a 0 is not a low rating; the guard limits the run to rated copies |
| No MBID → errors, not skips | `…::test_a_track_with_no_recording_mbid_is_an_error` | `UNMATCHED / no_recording_mbid`, counted as `err`, nothing recorded |
| No changes → no submissions | idempotence assertions in every direction | submission/love counts, not just outcomes |
| Round trip love → unlove → love | `test_plex_source.py::test_a_round_trip_love_then_withdrawal_then_love_again` | three runs, target converges, clears, converges |
| External love missing from Plex | `test_edge_cases.py::test_a_recording_missing_from_plex_is_an_error` | `UNMATCHED / no_match`, counted as `err` |
| Dry run is the real pipeline | `…::test_a_dry_run_takes_the_same_decisions_and_writes_nothing` | identical decisions, no writes, no records (contract §4) |
| Mass undo is refused | `…::test_a_mass_undo_is_refused_unless_the_run_allows_it` | 12 of 12 withdrawn → `MASS_UNDO_REFUSED`, then allowed explicitly (contract §3) |
| Two directions, one pair | `…::test_a_love_relay_and_a_hate_relay_do_not_reconcile_against_each_other` | each relay reconciles only its own records |
| Paging | `…::test_more_than_one_page_of_loves_is_read` | 150 loves read across two pages |
| A hate relay onto Last.fm | `…::test_a_hate_relay_cannot_target_lastfm` | every track fails visibly instead of becoming a love |
| The operator's `.config` | `…::test_the_run_uses_the_connected_service_not_the_operators_config` | settings are pinned; the seeded credential row is what connects |
| Contributor credential docs | dropped | nothing reads a live credential file; credentials are rows the fixture seeds |

## Divergence: target drift is not re-applied

RR-12 asks that a track unloved on ListenBrainz, or un-loved on Last.fm, be re-loved
"next sync, because Plex is the source of truth". The engine does not do that, and
plan [010](2026-11-15-010-dont-adopt-target-state.md) is why: once a record exists its
intent is compared, and an unchanged intent is `SKIPPED / already_synced`. Target state
is read to decide *whether a new write is needed*, never to police a write already made.

Both readings are defensible and they cost differently — re-applying drift means every
run rewrites what a user changed on three services by hand. The suite pins the current
behaviour in a test that names the ticket's expectation, so the day either side changes
it fails loudly.

**Resolved in [014](2026-11-16-014-reapply-target-drift.md).** The default is unchanged,
so both tests here still hold and are what they always were: the behaviour of a relay
that did not ask. A relay may now name `reapply_target_drift`, which re-writes only a
write the target has *dropped* — never a value the user replaced it with. The cost 013
feared turned out not to exist: planning already reads the target's state for every
track in the fetch set, so the check is a flag on data the run already had.

## Not covered (deferred, with the reason that outlives the deferral)

- **Rate limiting / connection failure.** Already unit-tested where the seam is
  (`tests/test_engine_retry.py`, `tests/test_http_timeouts.py`); an integration fake
  would be re-testing `attempt()` with extra steps.
- **Multiple Plex copies of one track.** One copy per test; the multi-copy path is
  `tests/test_engine_ownership.py`'s subject and its fake library is smaller.
- **Featured artists.** Rung behaviour, pinned in `tests/test_engine_matching.py`.

## Tests

`just test` runs them with everything else. `tests/integration/` holds `fakes.py`,
`conftest.py` and three modules (`test_plex_source.py`, `test_external_source.py`,
`test_edge_cases.py`). `pyproject.toml` ignores `tests` for pylint, so a mistake here
is caught by review and by the assertions, not by the linter.
