# 016 — A relay that carries the star rating itself

Follows HF-236 / RR-16, which let Hyperfix hold a rating for a track it had never
seen. That made the bridge complete and immediately showed what it was bridging:
**love/hate only**. Plex and Hyperfix both hold a real star rating — 0–5 in half
steps on both sides — and today the relay throws that away, classifying a track
as a love (`>= 8`), a hate (`<= 2`) or nothing at all, then writing the threshold
value instead of what the user chose.

This plan adds a third direction, `rating`, that relays each track's own value.

## Problem

`Intent` already carries a number (`IntentKind.RATING`), and `PlexTarget` already
writes arbitrary Plex ratings. What is fixed is *where the number comes from*: the
relay's threshold. Every source reads a set by comparison (`userRating>>=`, `<<=`,
hyperfix's feed filtered the same way), and every target writes that direction's
threshold — `8.0` to Plex, `80` to Hyperfix, a love to Last.fm, `+1` to
ListenBrainz. A track rated 3 stars is invisible to both directions.

## Decisions

1. **`rating` is a third direction, not a replacement.** `relay_identity` returns
   `"rating"`, so sync records land under their own relay key and every existing
   love/hate relay keeps its history. LFM and LBZ keep love/hate, which is all
   they have.

2. **The intent is the track's own rating.** `Track.rating` already travels from
   every source (Plex's `userRating`, hyperfix's feed ÷ 10). In `rating` mode a
   target ignores its configured threshold and writes `track.rating`.

3. **"Already holds it" becomes equality.** A love relay holds when the target is
   `>= threshold` and a hate relay when `<= threshold` (that band is what makes a
   user's own 5-star copy survive a love relay writing 8). With exact values the
   band is the value: Plex compares with `_same_rating`, hyperfix on its own 0–100
   scale. The pipeline's unchanged rule still protects the user: same intent as the
   record is `already_synced`, and a target with no record is theirs.

4. **The source set is "rated at all".** Plex reads `userRating>>=1` (half a star);
   hyperfix reads its feed, which maps a 0 to `0.1`, so `>= 0.1` is the same
   filter. No new comparison operator: `rating` mode passes those numbers.

5. **The engine's scale stays Plex's.** 0–10 in half steps, which is exactly what
   hyperfix's 0–100 divides into, so neither direction loses anything. A hyperfix
   rating that is not a multiple of 10 (the UI allows any integer) rounds when it
   is written to Plex, and is compared rounded — the same rule on both ends, so a
   round trip is stable.

6. **Capability is refused by kind, at config time.** Last.fm and ListenBrainz have
   no star scale: they cannot be the target of a `rating` relay and their sources
   cannot be read as one. Enforced with the existing `unsupported_target` and by
   the request schemas' `Literal`s, so the UI cannot offer it either.

7. **A clear is still a withdrawal.** Unrating a track removes it from the source's
   fetch, and the existing reconcile undoes *our own* write — it never clears a
   rating the user set on the target. No new machinery.

8. **Writing into Hyperfix keeps `source=relay`** (HF-236): a relayed star is shown
   and attributed on the release page, and stays out of the diary.

## Non-goals

- **One relay projecting to several targets.** A star relay to LFM/LBZ would have
  to threshold on the way in (`>= 4` → love). That is what the love relay is.
- **Hyperfix → Plex.** Hyperfix already writes its own ratings to Plex, and the
  pairing is refused for that reason. It also needs hyperfix's feed filtered to
  `manual` first (`?sources=`), or a rating cleared in Plex comes back from the
  reverse relay on the next run. That filter lands in this change; the relay that
  uses it does not.
- **Per-relay thresholds.** There is one love threshold and one hate threshold,
  shared, and `rating` mode reads neither.

## One mode per pair

Two relays on one pair that classify the same field differently fight: a love relay
writing `80` and a rating relay writing `100` to the same recording settle at
whichever ran first, and neither notices, because each compares against its own
record. The flow builder already pairs one source config with one target list, so
the UI cannot express it; a hand-built job or a second relay can. Documented in the
README; refusing it at job creation (a query over stored jobs) is a follow-up.

## Changes

| File | Change |
| --- | --- |
| `engine/services/plex.py` | `PlexSource`: `rating` mode reads `>= 1`. `PlexTarget`: takes `mode`; writes `track.rating`, holds on equality |
| `engine/services/hyperfix.py` | `HyperfixSource`: `rating` mode reads `>= 0.1`. `HyperfixTarget`: writes `_hundreds(track.rating)`, holds on equality |
| `engine/types.py` | `EXACT_MIN_RATING` constants with the "rated at all" numbers |
| `core/factory.py` | `relay_identity` accepts `rating`; `unsupported_target` refuses LFM/LBZ targets; sources refuse them too; rating mode never calls `plex_threshold` |
| `schemas/relay.py` | Plex/Hyperfix `feedback_type` gains `rating`; LFM/LBZ stay love/hate |
| `cli/main.py` | `--feedback-type rating` |
| `frontend/lib/components/flow/RatingNode.svelte` | third option beside loves/hates; threshold row hidden in that mode |
| `README.md`, `AGENTS.md` | the third direction, one mode per pair, what "exact" means |

## Tests

- A `rating` relay writes the source's own value, not the threshold, to both
  targets (Plex and Hyperfix), including a half-star (`8.5` → `85`).
- The same value twice is `already_synced`; a changed value is written again.
- A target holding a different value with no record is the user's (skipped), and
  with a record it converges.
- Unrating in the source undoes our write and leaves a user's rating alone.
- `relay_identity("plex", feedback_type="rating") == "rating"`, records do not
  collide with the love relay on the same pair.
- LFM/LBZ refused as a `rating` target and as a `rating` source; the API rejects
  the combination through the schema.
- CLI parses `--feedback-type rating`.
