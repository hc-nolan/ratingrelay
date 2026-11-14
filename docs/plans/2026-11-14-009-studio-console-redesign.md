# RR-5 — Studio console: a design system for the web app

Status: implementing (frontend only). No API, schema or engine change.

## Problem

The v3 web app was built page by page during the engine work, so every surface
invented its own styling:

- `routes/+page.svelte` carried a 510-line bespoke `<style>` block that no
  component could reuse (`.btn`, `.panel`, `.service-card`, …).
- Colours were literals (`bg-emerald-500`, `text-white`, `oklch(0.65 0.18 22)`
  inline on an error paragraph). A page could pick any green it liked.
- Three near-identical sans families were loaded and did nothing.
- The job log printed 432 unbounded rows; the unmatched page was 14 069 px tall
  with 200+ undifferentiated rows; job detail sat in a 640 px column inside a
  1440 px window and painted `0 ERRORS` red.

None of that is a data bug, so none of it showed up in the tests. It is a
maintainability and comprehension problem, and it is the last piece of v3.

## Direction: "Studio console"

A dense, calm, dark console: warm charcoal neutrals, one accent, hairline
borders, real connected nodes. Not a re-skin — a written-down token system the
components are built against.

Decisions taken with the user:

- **Dark only, permanently.** No theme switch, no `prefers-color-scheme`.
- **Warm charcoal**, not cool slate. The user asked to keep the old red tint;
  read as warm greys (hue ~45) with the cyan accent unchanged.
- **One typeface**: `@fontsource-variable/inter` for both body and headings.
  `nunito-sans` and `figtree` were deleted, not just left unused. Hierarchy
  comes from size and weight.
- **Status colours are tokens**: `--ok`, `--warn`, `--info`, `--destructive`.
  A page reaching for `bg-emerald-500` is a bug by policy.
- **Radius 10 px**, replacing the square (0 rem) corners.
- **Numbers line up**: `font-feature-settings: 'cv05' 1, 'tnum' 1` on `body`.
- The **flow builder is kept**, not replaced by a free-form node graph.

## Token system (`routes/layout.css`)

`@import` Tailwind, `tw-animate-css`, `shadcn-svelte/tailwind.css`, Inter;
`@plugin` forms + typography.

`:root` defines the palette in OKLCH, warm neutrals:

| token | role |
| --- | --- |
| `--background` / `--foreground` | page, warm charcoal on warm white |
| `--card` / `--popover` | the panel surface |
| `--muted` / `--muted-foreground` | flat fills, secondary text |
| `--accent` | hover surface (warm) |
| `--primary` | the single accent, cyan `oklch(0.735 0.122 197)` |
| `--destructive` | errors, destructive actions |
| `--ok`, `--warn`, `--info` | run/connection state |
| `--border` 9 %, `--input` 13 %, `--ring` primary @ 55 % | hairlines |
| `--radius` `0.625rem` + `--radius-sm…4xl` ladder | 10 px corners |

`@theme inline` maps those to Tailwind (`--color-ok`, `--color-destructive-foreground`,
the shadcn colour names, the radius ladder) and sets
`--font-sans` = `--font-heading` = Inter Variable.

`@layer base` sets `border-border`, `outline-ring/50`, `bg-background
text-foreground antialiased`, and the numeric font features.

`@layer components` holds only three shared classes, because everything else is
a component:

- `.eyebrow` — 12 px semibold uppercase, `tracking-[0.14em]`, muted.
- `.panel` — `rounded-lg border border-border bg-card`.
- `.control` — the one flat text control style (`rounded-md border bg-background`).

### Type scale

Four steps, no one-off sizes: `text-xs` (12, caps and meta), `text-sm` (14,
body/controls in the app), `text-base` (16, prose), `text-lg`/`text-xl`
(headings). Node names 18 px, page headings `text-lg`.

### Width rule

The canvas and the run dock share `max-w-4xl`, so the dock can never look like
a status bar bolted to something else.

## Component inventory

New primitives under `lib/components/ui/` (each with a barrel), chosen so pages
stop hand-rolling markup:

| primitive | why |
| --- | --- |
| `input` | flat `h-9` field, hairline border, `focus-visible:border-primary/60`, no ring |
| `checkbox` | hidden real `<input>` + drawn check, `peer-checked` only, no JS |
| `label` | the one label style |
| `native-select` | a native `<select>` (2–5 options) + our own chevron; `bg-none` strips the chevron `@tailwindcss/forms` paints |

`button` was kept but cleaned up: its `href` branch needed an explicit
`no-navigation-without-resolve` disable (callers resolve internal paths).

Shared app components:

- `AppHeader` — logo tile, wordmark, nav (`Relay` / `History` / `Unmatched`),
  unmatched count badge, `status` snippet slot (connection pills), sign-out.
  Active state from `page.url.pathname`, never a prop.
- `ActivityRail` — 320 px right rail: live count, "All runs" link, in-flight
  jobs, last seven finished. Replaces the collapsing `JobsRail`.
- `jobs/JobCard` — one row per run, `compact` for the rail.
- `flow/*` — `FlowNode` (card shell, eyebrow label, tone dot, ghost variant,
  actions slot), `SourceNode`, `TargetNode`, `RunDock`, `FlowLink`, `ServiceMark`.

`lib/format.ts` owns presentation of runs: `whenLabel`, `dayLabel`, `dayKey`,
`durationLabel`, `statusLabel`, `statusTone` → `Tone`, and the `TONE_DOT` /
`TONE_TEXT` / `TONE_CHIP` maps. One meaning per tone; a zero error count is
never red. `relay.ts#statusColor` now returns token classes and delegates.

## Page by page

### Shell (`+layout.svelte`)

Gate on the session, then render the page. Dark-only import. `AppHeader` is per
page (so the wizard can omit it).

### Setup wizard (`routes/+page.svelte`, `step !== 'done'`)

The bespoke 510-line style block is gone; the wizard is now tokens + primitives:

- Header: accent tile with three bars, wordmark, one-line subtitle.
- Progress rail: four numbered steps with a hairline connector; done steps turn
  `ok`, the active step takes `primary`.
- Three panels (Plex / Plex server / music services), each a `.panel` with
  `ServiceMark`, name, purpose, and a `TONE_CHIP` connection state.
- The active step takes `ring-1 ring-primary/25`; steps that are not reachable
  yet are dimmed and `pointer-events-none`.
- Errors are `text-destructive`, never an inline literal colour.

### Relay (home, `step === 'done'`)

`AppHeader` + full-bleed canvas (`min-h-0 flex-1 overflow-y-auto`, own scroll
container, nothing sticky inside) holding a vertical chain in `max-w-4xl`:

`SourceNode → FlowLink → TargetNode* → [ghost targets] → FlowLink → RunDock`

`RunDock` is a `FlowNode` labelled `Trigger`, so the run action is part of the
flow rather than a fixed bar. `ActivityRail` sits alongside.

### History (`/history`)

New route, so the home page stops being a job list. Status filters (all / live /
completed / needs a look), service select, search, day groups with counts,
`JobCard` rows, 25 per page with "show more", cancel in place.

### Job detail (`/jobs/[id]`)

Full width. Header panel with tone dot + status chip, dry-run badge, source →
targets, started/finished/duration. Four stat tiles (Fetched / Written or Would
sync / Failed / Skipped) where Failed is only red when it is non-zero. Progress
bar. Track log: one dense line per event, newest first, reason buckets
(all/written/skipped/failed/unmatched) with counts, search, 200 rows then
"show all". SSE keeps the page live; the counters come from the job row.

### Unmatched (`/unmatched`)

The worst surface before: 14 069 px, 200+ rows, no grouping. Now:

- Grouped by target service, biggest group first, `PER_GROUP = 12` behind a
  "Show all N (M more)" disclosure.
- Filter chips per service with counts + search across artist/title.
- Each row expands in place into the mapping form (candidate picker, artist,
  title, an MBID field that appears only for ListenBrainz, "apply to").
- Below the queue, three collapsed `<details>` panels: resolved by a mapping,
  the mappings themselves, and the records a reset cannot undo (`text-warn`).
- Same server contract as before; `CandidatePicker` untouched.

## Out of scope

- No API, schema, engine or CLI change.
- No new persistence, no user preferences, no density toggle.
- No theme switch and no light mode.
- No browser test suite; verification is `just check` plus CDP screenshots.
- No change to what a run does. This pass is presentation only.

## Verification

- `just test` (216), `just lint` (9.72), `just check` — all exit 0.
- `svelte-check`: 0 errors, 0 warnings (was 24 warnings, 18 eslint errors).
- Every surface screenshotted over CDP: shell, home, history, job detail,
  unmatched, and the setup wizard against a scratch database.

## Addendum — what changed after the first pass

Three of the decisions above were reversed while using the app, and the reasons
are worth keeping:

- **The accent is bronze, not cyan** (`oklch(0.71 0.126 58)`, with `--ring`,
  `--info`, `--chart-1` and the sidebar following it). Warm neutrals under a cool
  accent still read as two apps; a console wants one temperature. The service
  marks kept their brand hue and lost their cool backdrops, and `theme-color` in
  `app.html` went warm with them.
- **The typeface is IBM Plex Mono for everything**, not Inter. The grid is the
  point: counts, ratings and timings line up, and the drawn `a`/`g` keeps it
  from looking like a coding default. Inter is uninstalled.
- **`--radius` is 0.** The 10 px corners were the last thing making the app look
  like the shadcn default. Square everything, including cards and chips; only
  dots and the progress bar keep rounding.
- **`native-select` is gone.** A native `<select>` cannot have its popup styled,
  and in a browser that ignores `appearance: base-select` it also paints a
  second caret over ours. `ui/select` wraps the bits-ui listbox instead: square
  trigger, caret in a gutter, our own popover, rows, highlight and checkmark.
- **Targets are one card that switches on**, not a ghost row that turns into a
  separate `TO` node: the grid never reflows, the picked card gains a solid
  accent edge, a tinted fill and its own configuration, and neighbours stay put.
