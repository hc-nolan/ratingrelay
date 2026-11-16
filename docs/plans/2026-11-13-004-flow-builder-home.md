# A home page that looks like the thing it does

## The problem with the current home page

It is four stacked form cards — FROM, TO, SCHEDULE, DRY RUN — followed by a run
button and then the job list, all in one centred column. Every one of those is a
pastel rectangle with the same weight, so nothing says which parts are the
pipeline and which parts are settings. The relationship the tool exists for
(*this source writes to these targets*) is implied by reading two headings, and
the job list grows downward into the same scroll as the form, so a busy queue
pushes the controls off screen.

## What it becomes

A two-pane shell. The pipeline is a vertical flow of node cards on a dotted
canvas on the left; the jobs live in their own rail on the right, with their own
scroll, collapsible to a strip.

```
┌─────────────────────────────────────────────┬──────────────────┐
│  RatingRelay   [pills]           Unmatched  │ Jobs        3  ‹ │
├─────────────────────────────────────────────┤──────────────────┤
│  · · · · · · · · · · · · · · · · · · · · ·  │ ● plex → lbz     │
│    ┌ FROM ───────────────────────────────┐  │   359 ✓ 2 ✗      │
│    │ [Plex] [Last.fm] [LBZ] [Reset]      │  │──────────────────│
│    │ rated ≥ ★★★★☆                    ⌄  │  │ ● plex → lastfm  │
│    └─────────────────────────────────────┘  │   running 12/361 │
│                     │                       │──────────────────│
│                   ( + )                     │  (own scroll)    │
│                     ▼                       │                  │
│    ┌ TO ─────────────────────────────────┐  │                  │
│    │ ◈ ListenBrainz   love|hate      ×   │  │                  │
│    └─────────────────────────────────────┘  │                  │
│                   ( + )                     │                  │
│    ┌ + Last.fm ───────────────────────────┐  │                 │
│    └─────────────────────────────────────┘  │                  │
│                                             │                  │
│    [Repeat: Once ⌄]  [☐ Dry run]  [▷ Run]   │                  │
└─────────────────────────────────────────────┴──────────────────┘
```

Three things this buys:

1. **The pipeline reads as a pipeline.** Source at the top, targets below it,
   joined by a rail that carries the `+`. Adding a target is adding a node, not
   ticking a checkbox in a list that already lists every service.
2. **A target is either a node or a ghost.** Allocated targets are solid cards
   with their own config; the services you *could* add are dashed `+` ghosts.
   Clicking a ghost allocates it; the `×` returns it to the ghost list. That is
   the builder idiom, and it makes "off" visible without showing three
   greyed-out rows.
3. **The queue stops competing with the form.** It gets its own pane and its own
   scrollbar, and collapses to a strip when you are only here to change a rating.

## Deliberately not a node graph

The engine takes exactly one source. A free-form canvas with drag-and-drop would
promise fan-in that the pipeline cannot do, so the canvas is a vertical chain:
one source, N targets. The builder vocabulary (cards, ports, `+`, ghosts) buys
readability; inventing a graph would buy a lie.

## Layout and components

New, in `src/lib/components/`:

| File | Job |
| --- | --- |
| `flow/FlowNode.svelte` | one node card: label strip, body, optional danger tone and ghost state |
| `flow/FlowLink.svelte` | the connector: a vertical rail with a `+` button in the middle |
| `flow/ServiceMark.svelte` | the Plex / Last.fm / ListenBrainz marks, in one place instead of five |
| `flow/SourceNode.svelte` | FROM: service chips, then that service's fetch config |
| `flow/TargetNode.svelte` | TO: allocated target with config and `×`, or a `+` ghost |
| `flow/RunDock.svelte` | repeat frequency, dry run, and the one primary action |
| `jobs/JobsRail.svelte` | the aside: collapsible, own scroll |
| `jobs/JobCard.svelte` | one job: status dot, summary, progress, cancel |

The page keeps the state and the API calls; the wizard branch (`step !== 'done'`)
is untouched.

## shadcn-svelte

The repo had the shadcn CSS and `components.json` but **no primitives
installed**, contrary to `frontend/AGENTS.md`. This change installs the five the
layout actually needs — `button`, `card`, `badge`, `separator`, `collapsible`
(plus `bits-ui` and `tailwind-variants`, which they need) — and the new
components are Tailwind + those primitives. Native `<select>` stays for the
two- and five-option controls: it needs no JS and no icon dependency, and a
rating picker is not worth a combobox.

`statusColor()` in `$lib/relay.ts` returns Tailwind classes instead of the page's
bespoke `.status-*` rules, so the dot styling stops depending on the page's
`<style>` block.

## What does not change

- No API, schema or engine change. The payload built from the nodes is the same
  payload as before.
- Same state variables, so the wizard hand-off and the auth polling keep working.
- No new persistence: the rail's collapsed state is not remembered across loads.

## What landed

- Home markup replaced with the flow canvas (FROM node → rail → allocated TO
  nodes → rail `+` → ghost targets) plus the jobs aside and the run dock. The
  page's `<style>` block lost the 98 rules the old layout owned (1,208 → 502
  lines); `+page.svelte` went 2,648 → ~1,600 lines.
- **The `+` only appears where it does something.** With nothing allocated the
  ghost list *is* the picker, so the empty state shows no `+`; once a target is
  allocated, the rail `+` reveals the remaining ghosts.
- **Dark mode only.** The dark palette moved into `:root`, `.dark` is gone, and
  `app.html` carries `class="dark"` so `dark:` utilities in the vendored shadcn
  primitives still fire. `color-scheme: dark` makes native selects and
  scrollbars follow. There is no theme switch and no plan for one.
- `jobs/[id]` lost its own `.status-*` dot rules and now uses `statusColor()`
  like the rail does.
- **Scaled for a real screen, not a demo.** The first pass was built at
  `max-w-xl` with 11–12 px type and read as miniature at 1440 px. The canvas and
  dock now share `max-w-4xl` with a 16 px body: node names at 18 px, controls at
  16 px, rail marks at 24 px, list cards at 14 px, section caps at 12 px. The
  rule that follows: type sizes come from the four-step scale (`text-xs` through
  `text-xl`), never `text-[0.6875rem]`-style one-offs.

### Verified in a real browser

Headless Chromium over CDP, against the dev server and the live database:

| Step | Result |
| --- | --- |
| load | FROM = Plex, two ghosts, no trailing `+`, rail shows 15 real jobs |
| allocate ListenBrainz | solid TO node with Love/Hate, `×`, trailing `+` |
| click trailing `+` | the one remaining ghost appears |
| allocate Last.fm | both nodes, no `+` left |
| repeat = Weekly | the dry-run checkbox disappears (dry run is forced off) |
| dry run ticked | primary button reads "Preview run" |
| queue | POST body is the old payload: `{source:{service,rating_threshold,comparison}, targets:[{service},{service,feedback_type}], recurring:false, dry_run:true}` |
| collapse jobs | aside is 44 px, vertical "JOBS" strip, "Expand jobs" |

`just test` 140 passed, `just lint` 9.62/10, svelte-check 0 errors, prettier
clean, eslint **12 → 5** (the five are the documented v3 baseline, all in the
wizard or vendored `ui/`).
