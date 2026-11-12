---
date: 2026-05-10
topic: user-control-surface
focus: Relay configuration, thresholds, job monitoring, manual track mappings
mode: repo-grounded
---

# Ideation: User Control Surface

## Grounding Context

RatingRelay is a self-hosted FOSS tool (Python/FastAPI + SvelteKit 5) that syncs track ratings across Plex, Last.fm, and ListenBrainz. The "user control surface" covers everything the user touches to express their intent: relay configuration (source, targets, thresholds), job monitoring (SSE-streamed progress, event log), and manual track mappings (resolving unmatched tracks via artist/title or MBID).

Key infrastructure already in place: `SyncRecord` tracks what was synced per job; `TrackEvent` records per-track outcomes; `TrackMapping` stores manual overrides; `_resolve_mbid` performs MusicBrainz lookup; rate-limiting and LBZ 429 retry are already handled in the worker. The job runner exposes a `TrackCallback` hook that the SSE stream attaches to.

## Topic Axes

- A: Relay configuration UX
- B: Job monitoring & progress visibility
- C: Unmatched track resolution
- D: Notification & awareness

## Ranked Ideas

### 1. Dry-run mode
**Description:** Execute the full relay pipeline — source fetch, matching, deduplication — but skip all writes. Present results as "would have synced X tracks, skipped Y already-synced, failed to match Z." Implemented as a `dry_run: bool` field on the job; the worker's `on_track` callback hook suppresses the actual write calls.
**Axis:** A + B
**Basis:** `reasoned:` A mis-sync on Last.fm (e.g., accidentally loved 400 tracks) has no undo path. All fetch/match/dedup logic already exists; the only missing piece is a flag that skips the write calls.
**Rationale:** Directly addresses the most catastrophic failure mode for a tool whose value proposition is "faithful relay." Highest recurrence across all 6 ideation frames.
**Downsides:** A dry-run that doesn't exercise the actual write path can silently diverge from real behavior (e.g., rate-limiting only surfaces during writes). Users may run dry-run once then trust it permanently without re-verifying.
**Confidence:** 92%
**Complexity:** Low–Medium
**Status:** Explored

### 2. Threshold visualization with live preview
**Description:** Before configuring or running a Plex relay, fetch the user's rating distribution and display a histogram (counts per star level). As the user adjusts the threshold slider in the wizard, the "X tracks will be synced" count updates live without any writes.
**Axis:** A
**Basis:** `reasoned:` The threshold slider is opaque — a user has no mental model of whether "8.0" captures 40 tracks or 400. The distribution data requires the same read-only fetch path already used in `fetch_plex`. The gap is surfacing it before the sync runs.
**Rationale:** Misconfigured thresholds are the most likely source of erroneous syncs. Surfacing distribution data before writes makes the relay faithful to actual intent.
**Downsides:** Requires a Plex API call at wizard-open time, adding latency. Only meaningful for Plex source (Last.fm and ListenBrainz are binary loved/hated).
**Confidence:** 88%
**Complexity:** Medium
**Status:** Unexplored

### 3. Smart mapping resolution
**Description:** The unmatched track form becomes a fuzzy search over MusicBrainz (using existing `_resolve_mbid`). Each unmatched track gets an auto-proposed mapping with a confidence score. High-confidence proposals are one-click accept; low-confidence ones prompt manual review. The unmatched queue becomes a "review proposals" queue rather than a dead-letter queue.
**Axis:** C
**Basis:** `direct:` `_resolve_mbid` already exists in `relay.py` and performs MusicBrainz lookup; `TrackMapping` and unmatched API routes already exist. The gap is that auto-proposals aren't surfaced in the UI and the form is free-text rather than search-driven.
**Rationale:** Unmatched tracks are the primary source of ongoing friction — every recurring sync adds to the list. Auto-proposals with confidence turn a blocking workflow into an optional review.
**Downsides:** Fuzzy proposals can be wrong. An accepted bad proposal writes an incorrect mapping that persists across all future syncs. Confidence floor + easy per-proposal rejection mitigates but doesn't eliminate this.
**Confidence:** 85%
**Complexity:** Medium
**Status:** Unexplored

### 4. Per-job "what changed" diff
**Description:** On the job detail page, show the sync result as a structured diff: tracks newly synced this run (net new writes), tracks already synced and skipped, tracks present in the previous run but absent this run. The `SyncRecord` table already stores `sync_key` per job, making the delta computable via a query.
**Axis:** B
**Basis:** `direct:` `SyncRecord` already tracks which tracks were synced in which job. A per-job diff is a query comparing this job's rows against the previous job's for the same relay config — no schema changes needed.
**Rationale:** Raw counts (`tracks_ok: 47`) tell the user very little for recurring syncs. "12 new, 45 already synced, 0 lost" tells them whether the relay is behaving correctly over time.
**Downsides:** "Dropped" detection requires stable relay config across runs; threshold changes produce misleading diffs until the next full run.
**Confidence:** 80%
**Complexity:** Low–Medium
**Status:** Unexplored

### 5. Completion notifications
**Description:** After any job completes, post a configurable push notification: tracks synced/skipped/failed counts, a flag if new unmatched tracks need attention, and a link to the job. Configurable via ntfy topic, webhook URL, or Pushover token in settings.
**Axis:** D
**Basis:** `reasoned:` A recurring sync that runs at 2am and produces 12 unmatched tracks is invisible without active UI polling. The job completion event already fires in the worker; the notification is a side-effect on that event. ntfy is free, self-hostable, and requires only an HTTP POST.
**Rationale:** Without notifications, recurring jobs are a black box — the opposite of "user stays in control." Notifications complete the awareness loop for unattended automation.
**Downsides:** Adds configuration burden. May be noisy if the user has many recurring jobs with frequent partial failures.
**Confidence:** 82%
**Complexity:** Low
**Status:** Unexplored

### 6. Per-track retry from job detail
**Description:** From the job detail event list, a "Retry failed" button creates a new targeted job covering only the failed tracks — no re-fetch of the full source needed. `TrackEvent` already records which tracks failed and on which target.
**Axis:** B
**Basis:** `reasoned:` Errors are often transient (rate limit, network timeout). A retry without re-fetching the full source is faster and cheaper. The existing job infrastructure and `TrackCallback` hook can support a job constrained to an explicit track list.
**Rationale:** Gives the user a direct recovery action for transient failures rather than requiring a full re-run.
**Downsides:** Structural failures (track doesn't exist on target service) will just fail again. UI needs to distinguish transient vs. structural errors to avoid futile retries.
**Confidence:** 74%
**Complexity:** Medium
**Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|----------------|
| 1 | Bidirectional relay | Already implemented — user can select Last.fm/ListenBrainz as source and Plex as target today |
| 2 | Time-windowed relay | Incremental sync already addresses recency; time windows add expense with low incremental value |
| 3 | Named relay presets | Scope overrun for single-user tool; complexity high relative to value |
| 4 | Plex webhook listener | Assumes HTTP reachability from Plex to RatingRelay that may not hold in self-hosted NAT setups |
| 5 | Zero UI / YAML CLI | Creates a parallel full interface to maintain |
| 6 | Parallel target execution | High engineering cost; sequential execution sufficient for single user |
| 7 | Rate limit countdown | Backoff already handled transparently; real-time state surfacing complex and noisy |
| 8 | Auto-queue after mapping | Adds flow complexity; user can trigger manually |
| 9 | Batch resolution by artist | Premature for current user volume |
| 10 | Job history re-run button | QoL below step-function floor; weaker than other B-axis ideas |
| 11 | Per-target status panel | Subsumed by per-job diff (#4) |
| 12 | Pre-flight unmatched review | Subsumed by dry-run (#1) |
