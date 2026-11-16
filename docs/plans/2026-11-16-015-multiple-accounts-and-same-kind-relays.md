# 015 — A second account of any service, and same-kind relays

Extends RR-9 / github#20 ("Plex → Plex relay"). Rereading it as what it actually
is: **one relay between two accounts of the same service** — Plex → Plex for a
second server, ListenBrainz → ListenBrainz or Last.fm → Last.fm when somebody
moves to a new account, and the same machinery for the copy-everything-to-a-new-
account case that is the reason to want it.

Two things RR-9 did not say, and this plan pins down:

1. **A second account of a service is optional and lives on the services page.**
   A fresh install is unchanged: three services, one account each, no notion of
   an account in the UI at all. Adding a second Last.fm account is a thing you
   may do, on `/services`, next to the first one.
2. **Same-kind relays are the general rule, not a Plex special case.** Plex →
   Plex, ListenBrainz → ListenBrainz, Last.fm → Last.fm are all the same
   problem: two credential sets of one kind, and a target that must not be
   confused with the source.

## Problem

`ServiceCredential` is keyed by the service *kind* (`ServiceName.plex`), and
every other layer treats a kind as the identity of an account: the sync store
keys records on `(source_service, target_service)`, mappings on `target_service`,
the unmatched queue on `target_service`, job JSON, `relays.toml`, and the flow
builder's `SourceId`/`TargetId` unions. One kind means one account, everywhere.

That single equation is what makes RR-9 impossible. It is also what would make
a naive implementation dangerous: if a second Last.fm account shared the ref
`lastfm`, a love relay writing to the new account would reconcile against — and
undo — writes made to the old one. That is the mass-undo failure in contract §3,
produced silently.

## Decision

### The identity of an account is its *ref*, a string that already exists

An account's ref is what every existing column, JSON body and TOML file holds
for it today:

| Account | Ref |
| --- | --- |
| the account that exists now (one per kind) | `lastfm` |
| a second account, added later, labelled `old` | `lastfm:old` |
| a second Plex server, labelled `attic` | `plex:attic` |

The bare kind stays the ref of the account that is already there, and holds it
permanently. Consequence: **nothing that is already stored needs to move.** Sync
records, mappings, unmatched rows, queued and historical job JSON and existing
`relays.toml` files all keep meaning exactly what they meant. The only migration
is the credential table itself.

The alternative — a structured `(kind, account)` pair everywhere — would touch
every one of those columns and every wire body, and would let a caller smuggle a
kind where a ref belongs ("just a string") while the compiler stayed quiet. One
opaque string, parsed and formatted in exactly one module, is the smaller
surface. The cost is that a ref must be produced by `account_ref()`, never by
`f"{kind}:{label}"` at the call site, and that a *wrong* ref is a silent
cross-account reconcile — so the parsing/formatting module and its validation are
the load-bearing part of phase A, not the plumbing.

### Refs are immutable; labels are not

- `ServiceAccount.id` is the ref, and is the primary key: `lastfm`, `lastfm:old`.
- `ServiceAccount.service` is the kind, kept as a column for capability checks
  (Last.fm has no hate; only Plex has servers, thresholds and ratings).
- `ServiceAccount.label` is the display name ("Main", "Old account"), editable.
- The suffix is *not* editable: renaming a ref would strand every sync record
  and every `relays.toml` on disk (a file the app cannot rewrite). Rename the
  label instead; delete and re-add if the ref itself is wrong.
- Deleting an account is a **soft delete** (`deleted_at`), so the ref stays
  taken. Reissuing a deleted ref would attach another account's writes to your
  old ones — the same "never re-point an existing record" rule as contract §6,
  one level down. A soft-deleted row is ignored by credential lookup and by the
  services page, and still resolves a name for the history and reset views.

### What pairings become legal

Refusal moves from kind to ref:

| Pairing | Today | After |
| --- | --- | --- |
| `plex` → `lastfm` | ok | ok |
| `plex` → `plex` (same ref) | refused | refused — same account |
| `plex` → `plex:attic` | refused | **ok** (RR-9) |
| `listenbrainz` → `listenbrainz:new` | refused | **ok** |
| `lastfm` → `lastfm:new` | refused | **ok** |
| `lastfm:hate-source` → `lastfm` | refused (`lastfm` is love-only) | refused, now on the *kind* |

`unsupported_target` keeps comparing kinds: a hate relay still cannot target any
Last.fm account, and `LOVE_ONLY_TARGETS` keeps working untouched in spirit.

### What must not change

- **Sync records stay keyed by the pair of refs**, so two accounts of one kind
  never reconcile against each other. `relay_identity` stays direction-only
  (`love`/`hate`): the pair already distinguishes accounts, and folding the ref
  into the relay identity would orphan records when a label is ever renamed.
- **Mappings** load for the target's ref, then its kind, then `all`, with the
  most specific winning. An existing kind-wide mapping keeps working on a new
  account, which is what "migrate to a new account" wants.
- **Mass-undo guard, dry run, resume, cancel, `ALREADY_ON_TARGET`** are all
  per-run and per-pair; none of them need to know about accounts.
- **Settings still win, for the primary account only.** `LASTFM_TOKEN`,
  `LISTENBRAINZ_TOKEN`, `PLEX_TOKEN` and friends name *one* account — the one
  whose ref is the bare kind. A second account is DB-only. Documented, not
  inferred: env vars cannot carry a label.
- **One user, one library.** Adding a second account is not multi-tenancy, and
  the session gate still recognises one Plex owner. The owner is the primary
  Plex account; the others are places to read or write, not identities.

### The one thing an operator must not do casually

A same-kind relay's target is *the same service family* as its source, so the
existing rules do the right thing and this plan adds nothing except the pairing:
`ALREADY_ON_TARGET` means a target that already holds a rating is never adopted
or overwritten (a second Plex server that already has ratings is left alone; an
empty one receives everything), a Plex copy rated between the thresholds is
neither held nor touched, and a withdraw on the source still only reverses what
this tool wrote on *that* target.

## Shape

### Phase A — engine and construction

- `ratingrelay/engine/refs.py` (new): `ServiceRef` (`kind`, `account`),
  `parse_ref`, `account_ref`, `is_primary`, plus the `KNOWN_SERVICES` tuple. No
  framework imports; the CLI, the API and the engine all use it.
- `engine/services/{plex,lastfm,listenbrainz}.py`: `service` stops being a class
  constant and becomes an instance field set from the ref (`PlexSource.service`,
  `MappedTarget.service` today are `field(default=..., init=False)` / class
  attributes). `MappedTarget.service` must be a constructor argument so
  `mappings()` loads that account's mapping scope.
- `core/factory.py`: `relay_identity`, `source_for_service`,
  `target_for_service`, `unsupported_target` take refs; the refusal becomes
  "same ref".
- `core/adapters.py`: build the ref from a request config (`kind` + optional
  `account`).

### Phase B — persistence

- `models/services.py`: `ServiceCredential` → `ServiceAccount`, PK `id` (the
  ref), columns `service`, `label`, `deleted_at`, plus today's credential fields.
- One Alembic revision: create the table, `INSERT ... SELECT` one row per
  existing credential with `id = service`, drop `servicecredential`. Downgrade
  reverses it. Review by hand — this is the migration that cannot be
  autogenerated.
- `db/credentials.py`: `get_credential(db, ref)`, `upsert_credential(db, ref, …)`,
  `delete_credential(db, ref)` = soft delete, `list_accounts(db, kind=None)`,
  `primary_ref(db, kind)`.
- `core/credentials.py`: settings override applies when the ref is primary.

### Phase C — API

- `schemas/auth.py` / `routes/auth.py`: `AuthCheckResult` becomes an account
  list (`id`, `service`, `label`, `status`, `username`, primary flag) instead of
  three fixed fields. `/api/auth/status` keeps a compatibility shape if the
  setup wizard depends on it.
- `routes/{lastfm,listenbrainz}.py`: `?account=` on read, `account` + `label` in
  the credential body; an `add` path that mints `kind:label`.
- `routes/plex.py`: server list and server selection are already the "which
  server" question — the same body gains `account`, and the PIN/PIN-cookie flow
  stays tied to the primary owner (a second Plex server is a *target*, not a
  second login).
- `routes/relay.py` / `schemas/relay.py`: every source/target config gains
  `account: Optional[str] = None`; `RelayRequest` validation refusals move to
  refs.
- New `routes/services.py`: `GET /api/services` returns every live account with
  status, for the services page and the flow builder. `DELETE` soft deletes.
- `routes/unmatched.py`, reset (`core/relay.py` `_RESET_TARGET_CONFIGS`): take a
  ref; reset stays per-ref, never per-kind.

### Phase D — CLI

- `relays.toml` and the flags accept `service[:account]` in `source` and
  `targets`, parsed with the same `parse_ref`, so a headless migration is
  `source = "lastfm"`, `targets = ["lastfm:new"]`.
- The "source is also a target" check compares refs.
- `relays.toml.example` and the README gain the second-account example.

### Phase E — frontend

- `$lib/types/auth.ts` and `relay.ts`: `SourceId`/`TargetId` stop being closed
  unions of three strings and become `{service, account}` options built from the
  account list.
- `/services`: one card per kind, listing its accounts; "Add another account"
  opens the existing form plus a label field. A fresh install shows exactly what
  it shows today.
- Flow builder: a source/target node shows the account label when a kind has
  more than one, and only then.

## Tests

- `refs`: parse/format round-trip, rejected labels (empty, `:`, `/`), primary
  detection.
- Engine: two Last.fm accounts in one database — a run to `lastfm:new` neither
  sees nor undoes records written to `lastfm`; same-ref pairing refused; hate
  relay to any Last.fm ref refused; a kind-wide mapping applies to a second
  account.
- Concurrency-free but real: plex → plex:attic with fakes, asserting the target
  got a *different* library than the source and that pre-rated target tracks
  come back `already_on_target` (not written, not recorded).
- Mirroring: a plex → plex:attic run with `reapply_target_drift` on re-writes a
  track the attic dropped, and leaves a track somebody re-rated there at 6
  alone (plan 014's line, now across two servers).
- Migration: an existing DB with one credential per service upgrades to three
  rows whose ids are the bare kinds, and reports identical auth status.
- CLI: `relays.toml` with `lastfm:new`, and the same-ref refusal.
- Frontend: `pnpm check`; no browser suite yet.

## Phases as one branch or six?

A → B is not separable in one push (the factory cannot take a ref the table does
not hold), and C needs B. Plan: **A + B in one branch** (`feat(engine)` +
`feat(db)`), then C (`feat(api)`), then D (`feat(cli)`), then E (`feat(frontend)`),
each behind the same plan and each leaving the app green. E is the only phase a
user sees, so the feature ships when E lands — but D is what makes it useful
unattended, so D before E.

## Settled

**A second Last.fm account asks for its password and stores only the MD5**, the
same as the first one. pylast can only derive the MD5 from a plaintext password,
and a second account is an opt-in act on the services page, so one more field in
a form the user already has is cheaper than a parallel auth path. The
alternative — a Last.fm auth token / scrobbler session key, holding no password
at all — is better in principle and awkward in pylast; if it ever matters it is
its own ticket, and it would apply to the first account too.

**A Plex → Plex relay with `reapply_target_drift` on is a mirror — of the writes
this tool made, not of the source.** The attic server stops being authoritative:
if it loses a write we recorded there, the next run puts it back. What it keeps
is plan 014's line — a *replacement* is a person's edit, so a track somebody
re-rated on the attic to 6 stays at 6 even though the source says 8. That is the
useful half of mirroring (an attic server is not a place people rate things, it
is a place ratings go and sometimes get wiped by a library rebuild) without
turning the relay into something that overwrites hand-edits. No new machinery:
the pairing becoming legal is all it needs, plus the test below.

## Open questions

1. **What does the reset node's account picker say?** Reset is a *source* in the
   flow builder: you choose `Reset`, then a chip row picks which service to
   clear (`plex` / `lastfm` / `listenbrainz` today, `+page.svelte`
   `resetOptions`). With two Plex servers that row needs one chip per *account*,
   because a reset clears the writes recorded against one target ref — clearing
   "Plex" when two servers exist would be ambiguous, and clearing both at once
   is exactly the whole-service wipe contract §3 refuses.

   The open bit is only the label, and it must not contradict the other node:

   | Chip | Reads as | Fails when |
   | --- | --- | --- |
   | `Plex · Attic` | account name first, kind as context | a label is long or repeats the kind |
   | `Attic (Plex)` | kind first, label in brackets | the common case (one account) shows brackets for nothing |
   | `Attic` | just the label | a bare label does not say which service it is |

   Leaning `Plex · Attic`, with the primary account's chip being the bare kind
   (`Plex`) so a one-account install is unchanged — which is the same rule refs
   already follow.

## Progress

- **A + B landed** (`feat(engine,db): address a service by its account ref`):
  `engine/refs.py`, the three service ports carrying a ref, `pairing_refusal`,
  mapping scopes ref → kind → `all`, `ServiceAccount` + migration, ref-keyed
  credential helpers, settings pinned to the primary account. Wire format, CLI
  and UI are untouched: a config with no `account` is the first account, so the
  app behaves exactly as it did.
- **C** (API: `account` on every config, `/api/services`, account-addressed
  reset), **D** (CLI `service[:account]`), **E** (services page, flow builder)
  outstanding. Until C lands, a run can only reach the primary account from the
  web app — the factory can already build a second one, which is what the tests
  above exercise.
