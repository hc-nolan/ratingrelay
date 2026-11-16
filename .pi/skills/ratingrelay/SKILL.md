---
name: ratingrelay
description: Working agreement for the RatingRelay repo (~/repos/mine/ratingrelay2, branch v3) — a self-hosted tool that syncs track ratings between Plex, Last.fm and ListenBrainz. Load at the start of any RatingRelay session, whether planning, coding, debugging or reviewing. Covers the dev environment, what must not regress, and the layering the v3 rewrite is moving toward.
---

# RatingRelay

Read `AGENTS.md` at the repo root first — it is the source of truth for commands,
layout, the behaviour contract and conventions. `STRATEGY.md` covers product intent.
This file is only the session-level working agreement.

## Where things are

- Live checkout: `~/repos/mine/ratingrelay2`, on branch `v3`.
- v1 (`main`) is the original hand-written CLI: `~/repos/mine/ratingrelay`. Read it for
  the reconciliation logic and identity rules; do not revive its code.
- Both GitHub remotes: `gh` in the v1 checkout, `origin` here. Ignore the dead
  `dev.hnolan.ca` remote in the v1 checkout.
- Tickets are tracked in Hyperfix, project `RR`. Ticket numbers are bookkeeping only:
  **no design decision may be conditioned on Hyperfix integration.** Headless mode is
  a RatingRelay feature, not an integration point.

## Start of session

1. `git -C ~/repos/mine/ratingrelay2 status` — `v3` may carry unpushed work.
2. `just --list` if you are unsure the Justfile changed.
3. `just dev` to have both servers up (API `:8100`, UI `:5173`). If a port is taken,
   fix `.config`/`BACKEND_URL` rather than killing the other process — `:8000` and
   `:3000` belong to a different project on this machine.
4. `just test` to confirm the baseline is green before changing engine logic.

## Guardrails

- `.config` holds real credentials and is gitignored; never print, echo or commit it.
  v1's `~/repos/mine/ratingrelay/config.env` holds the *actual* Plex/Last.fm/LBZ
  credentials — never copy them into a test, a fixture or a commit.
- Tests use fake service clients. Never point a test at a live account: the v1 test
  suite did, and it wiped real ratings.
- Anything that writes to a target service (love/unlove/scrobble/rate) is destructive
  at scale. Prove changes through dry run first, and keep reset narrow.
- The frontend and the API surface are the public face of this project (it has users
  from the Plex community). Don't leave a page in a worse state than you found it:
  `just check` clean, components extracted, no hand-rolled one-off styling where a
  shadcn-svelte primitive exists.

## v3 phase

In progress: **dev environment + skills** (this setup), then splitting the relay out of
`ratingrelay/core/relay.py` into a headless-first `engine/` + `cli/`, with FastAPI and
SvelteKit as consumers. Order of work and known gaps are recorded in
`docs/plans/`. When you take on a chunk, write the plan into `docs/plans/` rather than
leaving it in the chat.

Behaviour that must survive the rewrite is listed under "Behaviour contract" in
`AGENTS.md`; if a refactor makes one of those impossible, stop and raise it instead of
quietly dropping it.

## Finishing

- `just test`, `just lint`, `just check` as applicable, judged by exit code.
- Conventional commit, scoped to the layer you touched (`feat(engine): …`,
  `fix(frontend): …`).
- Branch and PR into `v3`; only merge to `main` when the result is production-ready.
- If you learned something non-obvious (host quirk, library surprise, trap), record it
  in `AGENTS.md` or a comment at the code — not just in the chat.
