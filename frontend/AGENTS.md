## References

- shadcn-svelte: https://www.shadcn-svelte.com/llms.txt

---

## Project Configuration

- **Language**: TypeScript
- **Package Manager**: pnpm
- **Add-ons**: prettier, eslint, tailwindcss, sveltekit-adapter, mcp

---

You are able to use the Svelte MCP server, where you have access to comprehensive Svelte 5 and SvelteKit documentation. Here's how to use the available tools effectively:

## Available Svelte MCP Tools:

### 1. list-sections

Use this FIRST to discover all available documentation sections. Returns a structured list with titles, use_cases, and paths.
When asked about Svelte or SvelteKit topics, ALWAYS use this tool at the start of the chat to find relevant sections.

### 2. get-documentation

Retrieves full documentation content for specific sections. Accepts single or multiple sections.
After calling the list-sections tool, you MUST analyze the returned documentation sections (especially the use_cases field) and then use the get-documentation tool to fetch ALL documentation sections that are relevant for the user's task.

### 3. svelte-autofixer

Analyzes Svelte code and returns issues and suggestions.
You MUST use this tool whenever writing Svelte code before sending it to the user. Keep calling it until no issues or suggestions are returned.

### 4. playground-link

Generates a Svelte Playground link with the provided code.
After completing the code, ask the user if they want a playground link. Only call this tool after user confirmation and NEVER if code was written to files in their project.

---

## Project specifics

- **Dark only.** The palette lives in `:root` in `src/routes/layout.css` and
  `src/app.html` sets `class="dark"` so `dark:` variants in the vendored
  primitives still apply. There is no light theme and no theme switch — do not
  add one; new UI uses the tokens (`bg-card`, `text-muted-foreground`,
  `border-border`), never literal colours.
- **Nothing blue.** The neutrals and the single accent are both warm (the accent
  is a bronze at hue ~58). Cyan, blue and the browser's own cool dark menus read
  as a colder app on top of warm charcoal.
- **One typeface, and it is monospaced**: IBM Plex Mono, for body and headings
  alike. Fixed-width is the point — counters, ratings and timings sit in
  columns — and the drawn terminals keep it from reading like a coding default.
- **Square by default**: `--radius` is `0`, so cards, panels, inputs, buttons and
  chips all come out square. Dots and the progress bar keep their rounding
  because they are not containers. Do not reintroduce a radius on one control.
- **Home is a flow builder**, not a form: `src/lib/components/flow/` owns the
  nodes, the connector and the run dock; `AppHeader` and `ActivityRail` frame
  every signed-in page. The page keeps state and API calls.
- **Reach for `src/lib/components/ui/` first** (`button`, `input`, `label`,
  `checkbox`, `select`, `badge`, `card`, `collapsible`, `separator`). A literal
  `<input>`/`<select>` or a hand-rolled `.btn` is a bug, not a shortcut.
- **There is no native `<select>` in this app.** The platform popup cannot be
  styled, and in a browser that ignores `appearance: base-select` it falls back
  to a control that paints a second caret over ours. `ui/select` wraps the
  bits-ui listbox: square trigger, caret in its own gutter, our popover, rows,
  highlight and checkmark — the same control everywhere.
- **Status lives in `src/lib/format.ts`**: `statusLabel`/`statusTone` → `Tone`
  (`ok|warn|err|info|muted`) → `TONE_DOT`/`TONE_TEXT`/`TONE_CHIP`. One meaning
  per tone, and a zero error count is never red. Do not pick a colour per page.
- **Four-step type scale** (`text-xs`, `text-sm`, `text-base`, `text-lg`/
  `text-xl`). No arbitrary sizes. Numbers use tabular figures app-wide.
- The canvas and the run dock share `max-w-4xl`; the canvas is its own scroll
  container (`min-h-0 flex-1 overflow-y-auto`) and nothing inside it is sticky.
- The canvas is a **vertical chain** (one source → N targets) on purpose: the
  engine takes exactly one source, so a free-form graph would promise fan-in the
  pipeline cannot deliver.
- **Setup is a wizard until it is finished**: `/` shows the Plex → server →
  music → start flow when the install is incomplete, and the relay screen once
  Plex + a server + one music service are connected. Moving to the relay is an
  explicit "Start relaying" — never a side effect of connecting a service.
- **Credentials are managed at `/services`** after setup: one panel per service
  with replace and disconnect. Disconnecting deletes the `ServiceCredential` row
  and nothing else; sync records stay, because they are the only proof the relay
  owns a rating over there. Plex is the exception the UI warns about, since the
  session cookie is signed with its token.
