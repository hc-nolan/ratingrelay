# The unmatched queue said "no match" and offered nothing

Reviewed the live database and the queue after the v3 live pass, because the
unmatched list was full of tracks — The Smiths, Joy Division — that plainly do
have MusicBrainz recordings. Three separate faults were behind it, and only one
of them was matching.

## What the data said

`ratingrelay.db` held 4,285 `trackevent` rows spanning 2026-05-09 → 2026-11-13:

| reason | rows |
| --- | --- |
| `lastfm` success | 1,848 |
| `listenbrainz` success | 997 |
| `listenbrainz` withdrawn | 660 |
| `lastfm` `unrecorded_target` | 432 |
| `listenbrainz` `no_recording_mbid` | 326 |
| `lastfm` withdrawn | 22 |

The newest failure in the whole table was **2026-05-09**. Every Smiths row in the
screenshot was from that v2-era run and had synced successfully on every run
since. Re-running today's resolver over the 225 distinct pairs flagged
`no_recording_mbid` resolved **192** of them without any change to the resolver.

## Fault 1 — the queue was a history, not a state

`GET /api/relay/unmatched` selected only failed events and deduplicated them, so
a failure was listed forever: nothing a later run did could take it off, and a
track that failed in May buried the ones failing today. The count then read
"1× failed" for a track that had failed once four months ago.

Now every event is read newest-first and the **newest event per
(artist, title, target) decides whether the track is listed**. A track that has
synced since it failed disappears; one that fails again comes back with a count
of the failures since its last success. `last_failed` is returned so the UI can
show when.

## Fault 2 — production credits were treated as part of the title

`variant_form` already dropped `(feat. X)`, `(Remastered)`, `[Live]` and a
`- Remastered` dash suffix. `(Prod. Flying Lotus)` and `(Produced by X)` were
kept, so the title never compared equal to MusicBrainz's. Both are now edition
noise, matched as whole words so `Prodigy`, `Product` and `Proud` are untouched.

`normalize`/`track_key` are deliberately **not** changed: they key `sync_record`
and `track_mapping`, and v2 byte-compatibility there is pinned by tests.

## Fault 3 — the lookup threw away what it nearly found

`musicbrainz._lookup` runs the matching ladder with `allow_fuzzy=False`, and
correctly so: a recording MBID is what ListenBrainz feedback is keyed by, so a
fuzzy guess loves the wrong recording. But it returned `Optional[str]`, so the
variant and containment proposals the ladder had already computed were discarded
and the queue was left saying "no MBID" with nothing to click.

The lookup now returns a `Resolution(mbid, proposals)`. The write rule is
unchanged — only the exact tier produces an MBID — but a miss carries the
candidates, and `TrackOutcome.matches` flows them to `trackevent.candidates`
and on to the browser. `_candidates_json` now includes `recording_mbid`, because
a name alone cannot be turned into a ListenBrainz mapping without looking the
recording up a second time.

The frontend renders them: expanding a row shows "Did you mean…" as buttons that
fill the mapping form (`$lib/components/CandidatePicker.svelte`). Picking fills
the fields; saving is still a human act, which keeps a proposal a proposal.

## Rejected: `strict=True` on the MusicBrainz search

`musicbrainzngs.search_recordings` defaults to OR across fields, so cover
versions outrank originals and a query for "Love Will Tear Us Apart" returns
Airs and Simbolo before Joy Division. `strict=True` builds an AND/phrase query
and does rank the original first — but it is not an improvement here:

- **It does not resolve more.** On the 33-row probe the tier counts were
  identical under OR and AND (26 exact, 7 none). Ranking was never what decided:
  the exact rung is applied to the results regardless of order.
- **It breaks on quotes.** The strict builder interpolates the value into a
  quoted Lucene clause unescaped, so `Hand in Glove (7" version)` searches for a
  malformed query and returns nothing.
- **It breaks on artist spelling.** `billy woods & Kenny Segal` on a *billy
  woods* album returns 0 rows under AND, where OR returns the right recording.

OR plus the exact rung is the honest combination: ranking noise is filtered out,
and a candidate that survives is compared properly.

## What is still missed, and why

The hard misses share one shape: MusicBrainz files the recording under a
**different primary artist credit** than Plex does.

| Plex | MusicBrainz |
| --- | --- |
| Echo And The Bunnymen | Echo & the Bunnymen |
| Conway the Machine | Conway |
| billy woods & Kenny Segal | billy woods |
| DJ Muggs vs. GZA | GZA |
| Al Divino | Al.Divino |

`_containment_matches` requires the artist forms to be equal, so these are
unreachable by design, and loosening it would let tribute acts through — the exact
regression v2 had. A future rung could retry the search with the first credit of a
collaboration (`billy woods`) and accept a candidate whose artist is a *member* of
the source credit — as a proposal only, never as a write. That is a ticket, not a
line of code to sneak in here.

## Tests

- `tests/test_unmatched_route.py` — the queue drops a track that has synced since
  it failed, and takes it back when it fails again.
- `tests/test_engine_matching.py` — production credits are edition noise, and
  whole-word matching leaves `Prodigy`/`Product` alone.
- `tests/test_engine_musicbrainz.py` — a miss keeps the recordings it nearly
  matched; an exact match reports no proposals.
- `tests/test_engine_pipeline.py` — the ListenBrainz target carries the resolver's
  proposals into an unmatched outcome.
- `tests/test_worker_candidates.py` — no candidates is `NULL`, not `[]`, and a
  candidate keeps its `recording_mbid`.

## Live verification

A dry `plex → listenbrainz` job run against the real accounts (16 min, see below)
after the changes:

```
{"status":"partial","tracks_fetched":361,"tracks_ok":359,"tracks_err":2}
```

`GET /api/relay/unmatched` then returned **2 rows instead of 225** — the two
tracks that are actually unmatched, both `no_recording_mbid`, both failed once
in this run:

```
YAYAYI – Awesome | listenbrainz | no_recording_mbid | count=1
    -> Amai – Awesome [88253524-1b51-4af3-b534-d7c76a33228b]
slr – temp       | listenbrainz | no_recording_mbid | count=1
```

That first row is the whole point of the proposal work: the row names a
recording with an MBID, so the mapping form can be filled from it instead of
retyped. And it is a good example of why proposals are never written — Amai is
not YAYAYI, and only a human can say whether that is the track.

## The cost of a reset: 16 minutes

This run was cold because the ListenBrainz reset in the v3 live pass deleted the
`syncrecord` rows, and with them the recording MBIDs that had been resolved for
those 361 tracks. MusicBrainz allows one request per second, so every track needed
its own lookup again: ~353 sequential lookups is a 16-minute run, against 35
seconds warm.

The MBID is not really part of "what we wrote" — it is a cache of what the track
*is* — so a reset should not have to throw it away. Ticket filed to keep the
resolved MBID outside the intent record.
