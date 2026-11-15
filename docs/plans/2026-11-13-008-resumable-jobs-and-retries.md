# API hardening: resumable jobs and retries (RR-4)

Status: done. Closes the two items of RR-4 that were still open; the other two landed
earlier on this branch under their own headings.

## What the ticket asked for

1. The API has no authentication at all (`settings.secret` declared and unused) yet can
   queue a destructive reset. Add session/token auth.
2. Worker and SSE are single-process with no mid-job progress persistence; a restart
   loses in-flight state.
3. Cancel only works before the run starts; no retry/backoff for transient failures.

## What was already done, and where

- **Item 1 — auth.** Gaps work: `fbaf100` (Plex PIN login, `session_guard`, one identity
  = the library owner), then `26e9865`, which fixed the login signing the cookie with a
  token the guard could not verify against.
- **Item 2 — the SSE half.** `a94b949`: the stream tails the database instead of an
  in-process queue, so it works with the worker in another process. Per-track commits
  mean a crash keeps the history of the writes that really happened.
- **Item 3 — mid-run cancel.** `a94b949`: `job.cancel_requested`, observed at a track
  boundary *after* the track's event and sync record are committed.

## What this slice adds

### A restarted job resumes

Before: a `running` job found at startup went back to `queued`, then ran from track 0
with counters reset to zero and every already-settled track journalled a second time.
The job page showed a run twice as long as the run was.

Now (`core/worker.py`):

- `started_at` is set only if it is empty, so the second attempt keeps the first
  attempt's start time — it is one run.
- The counters continue from the job row instead of restarting at zero.
- An `already_synced` outcome on a resumed attempt is neither journalled nor counted:
  it is the echo of work the attempt that died already reported. Skips that are *news*
  (the `unrecorded_target` refusals, where a v2 record does not say what was written)
  are still recorded — those did not happen before, they are why the reset refused.
- Final status comes from the accumulated counters rather than this attempt's results,
  so "everything failed" still means a failed job.

No migration: `started_at` already meant "this run began".

### Bounded retries

New `engine/retry.py`: `attempt(call, what)` — three tries, doubling backoff from 0.5 s.

The rule is about transport, not text: `OSError` is retried (that is what `requests`
raises for a refused connection, a reset or a timeout) and `ValueError` never is (that
is a refusal the engine decided on). Retrying is safe because every call the engine
makes is idempotent — setting the same love twice is setting it once, and a fetch is a
read.

Wired into every network call the engine makes:

| call | on persistent failure |
| --- | --- |
| `source.fetch` | run fails (`failed` job) — the one thing that cannot be a per-track result |
| `target.plan` (`_apply`) | that track is `failed/write_failed`; the run continues |
| `plan.commit` (`_apply`) | that track is `failed/write_failed`, and nothing is recorded in the sync store |
| `target.undo` (`_undo`, and the reset path in `core/relay.py`) | that record is `failed/write_failed` and stays recorded |

`target.undoable()` is deliberately not wrapped: it is synchronous and side-effect free,
so there is nothing to retry.

## Tests

- `tests/test_worker_resume.py` (3): counters and journal continue across a restart,
  a first attempt still journals `already_synced`, and a resumed job can still be
  cancelled (including while its outcomes are being suppressed).
- `tests/test_engine_retry.py` (7): retry until it answers, give up after `ATTEMPTS`,
  never retry a `ValueError`, a flaky commit still gets recorded, a commit that keeps
  failing fails the track once and records nothing, a flaky fetch is retried, and a
  target whose plan is unreachable fails every track without failing the run.

## Live verification

Against a copy of the live database (`/tmp/rr4.db`), real Plex library, real pipeline,
**dry run only** — nothing written to Last.fm. Two jobs with the same config: one
marked as an interrupted attempt that had got three writes in, one fresh.

```
resumed: status=completed fetched=361 ok=16 err=0 skipped=10 events=17 started_at=<5 min ago>
fresh  : status=completed fetched=361 ok=13 err=0 skipped=352 events=365 started_at=<now>
```

Reading it:

- The resumed job kept its `started_at` and added its 13 writes to the 3 the first
  attempt had made. Its 10 skips are the 6 it started with plus the 4 `unrecorded_target`
  refusals — 352 `already_synced` echoes produced **one** event between them.
- The fresh job recorded everything: 13 + 352 = 365 events.
- `ok=13` in both matches the CLI dry run's 13 and the last real run's 13, which is the
  dry-run promise holding under a resume.

## Deliberate leaves

- **No attempt counter on the row.** A job that crashes three times in a row will be
  resumed three times, and nothing stops it. A real crash loop needs the reason it
  crashed, not a number; the log has that.
- **Retries do not apply to the periodic worker loop**, only to engine calls — the loop
  already sleeps on unexpected errors.
- **`plan()` failing per track is a change of semantics**, chosen on purpose: a target
  that cannot be asked about *one* track should cost that track, not the report on the
  360 that came before it. A systematically broken target still fails every track and
  so still fails the job.
