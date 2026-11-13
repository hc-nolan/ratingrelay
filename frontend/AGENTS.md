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
- **Home is a flow builder**, not a form: `src/lib/components/flow/` owns the
  nodes, the connector and the run dock, `src/lib/components/jobs/` owns the
  collapsible queue rail. The page keeps state and API calls; the setup wizard
  below `{step === 'done'}` is a separate, older surface — leave it alone
  unless a ticket says otherwise.
- The canvas is a **vertical chain** (one source → N targets) on purpose: the
  engine takes exactly one source, so a free-form graph would promise fan-in the
  pipeline cannot deliver.
