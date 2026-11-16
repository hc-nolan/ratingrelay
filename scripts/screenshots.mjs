/**
 * Refresh the README screenshots from the demo database.
 *
 *     just screenshots          # serves the demo stack, captures, tears it down
 *     node scripts/screenshots.mjs   # against a demo already running on :5174
 *
 * Playwright drives a real Chromium so the pages are captured as a person sees
 * them: a target has to be picked on the relay page, the partial run opened, and
 * the ambiguous track expanded, none of which is in the URL. Every value shown
 * comes from `scripts/demo.py`, so nothing here talks to a real service. Each
 * page is asserted on before it is captured: a screenshot of an empty state is a
 * silent failure, not a smaller one.
 *
 * `playwright-core` is a frontend dev dependency; it is resolved from there
 * because the repo has no root package.json. Any Chrome or Chromium works —
 * `playwright-core` downloads no browser of its own:
 *
 *     CHROMIUM=/path/to/chrome just screenshots
 */
import { createRequire } from "node:module";

const require = createRequire(
  new URL("../frontend/package.json", import.meta.url),
);
const { chromium } = require("playwright-core");

const BASE = process.env.BASE ?? "http://localhost:5174";
const OUT =
  process.env.OUT ?? new URL("../docs/screenshots/", import.meta.url).pathname;
const VIEWPORT = { width: 1280, height: 860 };

async function launch() {
  const executablePath = process.env.CHROMIUM;
  if (executablePath) return chromium.launch({ executablePath });
  // A stock Chrome is the likeliest thing on a machine that already runs this;
  // /usr/bin/chromium covers the distributions that ship one.
  const failures = [];
  for (const options of [
    { channel: "chrome" },
    { executablePath: "/usr/bin/chromium" },
  ]) {
    try {
      return await chromium.launch(options);
    } catch (error) {
      // "Chrome is installed but will not start" (a missing shared library, a
      // refused sandbox) must not be reported as "no browser here".
      failures.push(
        `${JSON.stringify(options)}: ${error.message.split("\n")[0]}`,
      );
    }
  }
  throw new Error(
    `Could not launch a browser; set CHROMIUM=/path/to/chrome:\n${failures.join("\n")}`,
  );
}

/** The demo run that shows all three outcomes, which is what the run page is for. */
async function interestingJob() {
  const res = await fetch(`${BASE}/api/relay/jobs`);
  if (!res.ok)
    throw new Error(`${BASE}/api/relay/jobs: ${res.status} ${res.statusText}`);
  const jobs = await res.json();
  const runs = (
    Array.isArray(jobs) ? jobs : (jobs.items ?? jobs.jobs ?? [])
  ).filter(
    (job) =>
      // A running job opens an SSE stream, so `networkidle` would never settle.
      ["completed", "partial"].includes(job.status) &&
      !job.dry_run &&
      job.tracks_fetched > 0,
  );
  runs.sort((a, b) => (b.tracks_err ?? 0) - (a.tracks_err ?? 0));
  if (!runs.length)
    throw new Error("No completed run in the demo database to screenshot.");
  return runs[0].id;
}

const browser = await launch();
const page = await browser.newPage({
  viewport: VIEWPORT,
  deviceScaleFactor: 1,
  colorScheme: "dark",
  reducedMotion: "reduce",
});
page.on("console", (msg) => {
  if (msg.type() === "error") console.error(`  console: ${msg.text()}`);
});

/** Webfonts and the API's first paint both land after `load`. */
async function settle() {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
}

async function shot(name) {
  await settle();
  const path = `${OUT}/${name}.png`;
  await page.screenshot({ path });
  console.log(`  ${name}.png`);
}

async function open(route) {
  await page.goto(`${BASE}${route}`, { waitUntil: "networkidle" });
  await settle();
}

/** Something an empty or stale instance would not have on the page. */
async function expectVisible(locator, what) {
  try {
    await locator.first().waitFor({ state: "visible", timeout: 10_000 });
  } catch {
    throw new Error(`${what} is not on the page at ${page.url()}`);
  }
}

try {
  // The relay page: a target has to be switched on for the flow to read as a
  // relay rather than as a menu of empty slots.
  await open("/");
  for (const name of ["Last.fm", "ListenBrainz"]) {
    const add = page.getByRole("button", {
      name: `Add ${name} as a destination`,
    });
    if (!(await add.count()))
      throw new Error(`No "${name}" target card on the relay page`);
    await add.first().click();
    await expectVisible(
      page.getByRole("button", { name: `Remove ${name}` }),
      `the picked ${name} destination`,
    );
  }
  await shot("relay");

  await open("/history");
  await expectVisible(
    page.locator('a[href*="/jobs/"]'),
    "a run in the history",
  );
  await shot("history");

  await open(`/jobs/${await interestingJob()}`);
  await expectVisible(page.getByText("Track log"), "the track log of the run");
  await shot("run");

  // The unmatched page: open the track that has candidates, because picking one
  // is the only thing that page does.
  await open("/unmatched");
  const row = page
    .locator("button[aria-expanded]", { hasText: "candidates" })
    .first();
  if (!(await row.count()))
    throw new Error("No unmatched track with candidates to expand");
  await row.click();
  await expectVisible(
    page.getByText("Did you mean"),
    "the candidates of the opened track",
  );
  await shot("unmatched");

  await open("/services");
  for (const name of ["Plex", "Last.fm", "ListenBrainz"]) {
    await expectVisible(
      page.getByText(name, { exact: true }),
      `the ${name} service`,
    );
  }
  await shot("services");
} finally {
  await browser.close();
}
