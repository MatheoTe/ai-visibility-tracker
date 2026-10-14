// Export the read-only snapshot behind the static live demo (NEXT_PUBLIC_DEMO_MODE=1).
//
// Reads everything the dashboard shows from a running, seeded backend through the
// public API (the same responses the UI would get), and writes it to public/demo/:
//
//   snapshot.json        me, brands, prompts, overview per range × model, sources per range, runs
//   prompts/<id>.json    the latest answers to each prompt (drill-down)
//   runs/<id>.json       run detail + every task of the run (run monitor)
//
// Usage (backend seeded with `uv run avt-seed`, API on :8000):
//   API_URL=http://localhost:8000 node scripts/export-demo-snapshot.mjs
//
// Dates in the snapshot are absolute; the demo client shifts them so `as_of`
// lands on the viewer's today and the data never looks stale.
import { mkdir, rm, writeFile } from "node:fs/promises";

const API = `${process.env.API_URL ?? "http://localhost:8000"}/api/v1`;
const EMAIL = process.env.DEMO_EMAIL ?? "demo@avt.dev";
const PASSWORD = process.env.DEMO_PASSWORD ?? "demo-password";
const RESPONSE_DAYS = Number(process.env.DEMO_RESPONSE_DAYS ?? 7); // drill-down history kept per prompt
const OUT = new URL("../public/demo/", import.meta.url).pathname;
const RANGES = { "7d": 7, "30d": 30, "90d": 90 }; // must match src/lib/dates.ts

let token = "";
async function get(path) {
  const res = await fetch(`${API}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status} ${await res.text()}`);
  return res.json();
}

/** Every item of an offset-paginated endpoint. */
async function allPages(path, limit) {
  const items = [];
  for (let offset = 0; ; offset += limit) {
    const sep = path.includes("?") ? "&" : "?";
    const page = await get(`${path}${sep}limit=${limit}&offset=${offset}`);
    items.push(...page.items);
    if (items.length >= page.total || page.items.length === 0) return items;
  }
}

/** Every item of a cursor-paginated endpoint, optionally stopping early. */
async function allCursor(path, limit, keep = () => true) {
  const items = [];
  let cursor = null;
  for (;;) {
    const sep = path.includes("?") ? "&" : "?";
    const page = await get(`${path}${sep}limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
    const kept = page.items.filter(keep);
    items.push(...kept);
    if (!page.next_cursor || kept.length < page.items.length) return items;
    cursor = page.next_cursor;
  }
}

const iso = (d) => d.toISOString().slice(0, 10);
function rangeEnding(asOf, days) {
  const to = new Date(`${asOf}T00:00:00Z`);
  const from = new Date(to);
  from.setUTCDate(to.getUTCDate() - (days - 1));
  return { from: iso(from), to: iso(to) };
}

const login = await fetch(`${API}/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
});
if (!login.ok) throw new Error(`login failed (${login.status}) - is the backend seeded? (uv run avt-seed)`);
token = (await login.json()).access_token;

const me = await get("/auth/me");
const workspace = me.workspaces[0];
if (!workspace) throw new Error("demo user has no workspace");
const ws = `/workspaces/${workspace.id}`;
const asOf = iso(new Date());

const brands = await allPages(`${ws}/brands`, 200);
const prompts = await allPages(`${ws}/prompts`, 200);
const runs = await allPages(`${ws}/runs`, 100);

const overview = {};
const sources = {};
for (const [key, days] of Object.entries(RANGES)) {
  const { from, to } = rangeEnding(asOf, days);
  const q = `date_from=${from}&date_to=${to}`;
  overview[key] = { "": await get(`${ws}/dashboard/overview?${q}`) };
  for (const model of workspace.models) {
    overview[key][model] = await get(`${ws}/dashboard/overview?${q}&model=${encodeURIComponent(model)}`);
  }
  sources[key] = await allPages(`${ws}/dashboard/sources?${q}`, 200);
}

await rm(OUT, { recursive: true, force: true });
await mkdir(`${OUT}prompts`, { recursive: true });
await mkdir(`${OUT}runs`, { recursive: true });

const cutoff = rangeEnding(asOf, RESPONSE_DAYS + 1).from;
let responseCount = 0;
for (const p of prompts) {
  const responses = await allCursor(`${ws}/prompts/${p.id}/responses`, 100, (r) => r.day >= cutoff);
  responseCount += responses.length;
  await writeFile(`${OUT}prompts/${p.id}.json`, JSON.stringify({ responses }));
}

let taskCount = 0;
for (const r of runs) {
  const detail = await get(`${ws}/runs/${r.id}`);
  const tasks = await allCursor(`${ws}/runs/${r.id}/tasks`, 200);
  taskCount += tasks.length;
  await writeFile(`${OUT}runs/${r.id}.json`, JSON.stringify({ detail, tasks }));
}

const snapshot = {
  version: 1,
  as_of: asOf,
  generated_at: new Date().toISOString(),
  me,
  brands,
  prompts,
  runs,
  overview,
  sources,
};
await writeFile(`${OUT}snapshot.json`, JSON.stringify(snapshot));
console.log(
  `demo snapshot written to ${OUT}: ${brands.length} brands, ${prompts.length} prompts, ${runs.length} runs ` +
    `(${taskCount} tasks), ${responseCount} answers (last ${RESPONSE_DAYS} days), as of ${asOf}`,
);
